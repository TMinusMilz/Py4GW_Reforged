"""Fresh HR Mesmerway coordination layer.

Reused from KeySoJway: TeamCombatFocus/CombatSense, EnemyKnowledge + RC4
OfflineKnowledge, shared-memory whiteboard primitives and interrupt feasibility.
The eSurge/Mistrust/utility policy in this module is new.

Core policy:
- all offensive Mesmers stay inside the exact authoritative KeySoJway Adjacent packet;
- 4x Energy Surge uses projected shared Energy reservations inside that packet;
- first-contact enemy Energy is estimated conservatively from profession/RC4 data;
- Mistrust protects only the concrete live spell activation that is confidently
  observed targeting one of our allies; heals/self casts remain interruptible;
- Cry/Power/Cry of Pain share one interrupt reservation layer;
- optional Aneurysm creates a coordinated refill anchor: nearby drained foes are
  suppressed as eSurge primaries while the refilled anchor still has useful Energy;
- AoS/EVAS/Signet of Corruption are optional supported PvE slots, not required build skills;
- coordination fails open if SharedMemory is unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Generator
import math
from typing import TYPE_CHECKING, Callable, TypedDict

if TYPE_CHECKING:
    from Py4GWCoreLib.GlobalCache.shared_memory_src.AccountStruct import AccountStruct


class InterruptDispatchOptions(TypedDict, total=False):
    dispatch_guard: Callable[[], bool]
    on_dispatched: Callable[[], None]
    on_cancelled: Callable[[], None]


from Py4GWCoreLib import Agent, AgentArray, GLOBAL_CACHE, Profession, Range, Routines, Utils
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.Skill import Skill


Energy_Surge_ID = Skill.GetID("Energy_Surge")
Mistrust_ID = Skill.GetID("Mistrust")
Cry_of_Frustration_ID = Skill.GetID("Cry_of_Frustration")
Cry_of_Pain_ID = Skill.GetID("Cry_of_Pain")
Signet_of_Corruption_Kurzick_ID = Skill.GetID("Signet_of_Corruption_kurzick")
Signet_of_Corruption_Luxon_ID = Skill.GetID("Signet_of_Corruption_luxon")
Power_Spike_ID = Skill.GetID("Power_Spike")
Power_Drain_ID = Skill.GetID("Power_Drain")
Overload_ID = Skill.GetID("Overload")
Unnatural_Signet_ID = Skill.GetID("Unnatural_Signet")
Spiritual_Pain_ID = Skill.GetID("Spiritual_Pain")
Aneurysm_ID = Skill.GetID("Aneurysm")
Mind_Wrack_ID = Skill.GetID("Mind_Wrack")
Shatter_Delusions_ID = Skill.GetID("Shatter_Delusions")
Fragility_ID = Skill.GetID("Fragility")
Web_of_Disruption_ID = Skill.GetID("Web_of_Disruption")
Phantom_Pain_ID = Skill.GetID("Phantom_Pain")
Epidemic_ID = Skill.GetID("Epidemic")
Deep_Wound_ID = Skill.GetID("Deep_Wound")
Bleeding_ID = Skill.GetID("Bleeding")
Burning_ID = Skill.GetID("Burning")
Poison_ID = Skill.GetID("Poison")
Disease_ID = Skill.GetID("Disease")
Blind_ID = Skill.GetID("Blind")
Crippled_ID = Skill.GetID("Crippled")
Weakness_ID = Skill.GetID("Weakness")
Cracked_Armor_ID = Skill.GetID("Cracked_Armor")
Wandering_Eye_ID = Skill.GetID("Wandering_Eye")
Clumsiness_ID = Skill.GetID("Clumsiness")
Panic_ID = Skill.GetID("Panic")
Psychic_Instability_ID = Skill.GetID("Psychic_Instability")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Complicate_ID = Skill.GetID("Complicate")
Shatter_Enchantment_ID = Skill.GetID("Shatter_Enchantment")
Technobabble_ID = Skill.GetID("Technobabble")
Frustration_ID = Skill.GetID("Frustration")
Diversion_ID = Skill.GetID("Diversion")
Arcane_Conundrum_ID = Skill.GetID("Arcane_Conundrum")
Dazed_ID = Skill.GetID("Dazed")
Ebon_Battle_Standard_of_Wisdom_ID = Skill.GetID("Ebon_Battle_Standard_of_Wisdom")
Ineptitude_ID = Skill.GetID("Ineptitude")
Blood_is_Power_ID = Skill.GetID("Blood_is_Power")
Soul_Twisting_ID = Skill.GetID("Soul_Twisting")
Winter_ID = Skill.GetID("Winter")
Famine_ID = Skill.GetID("Famine")
Splinter_Weapon_ID = Skill.GetID("Splinter_Weapon")
Mantra_of_Frost_ID = Skill.GetID("Mantra_of_Frost")
Ray_of_Judgment_ID = Skill.GetID("Ray_of_Judgment")
Aegis_ID = Skill.GetID("Aegis")
_WISDOM_BENEFICIARY_EXCLUSIONS: dict[int, str] = {}
_wisdom_team_loadout_signature = ""

# Stage 1.44: serialize a hex mutation against Shatter on the same target.
_HEX_MUTATION_LOCK = 0x4D570180
_FRAGILITY_INVALIDATED_LOCK = 0x4D570181
_COORDINATED_HEX_IDS = frozenset(
    (
        Mistrust_ID,
        Mind_Wrack_ID,
        Fragility_ID,
        Web_of_Disruption_ID,
        Phantom_Pain_ID,
        Frustration_ID,
        Ineptitude_ID,
        Wandering_Eye_ID,
        Clumsiness_ID,
        Arcane_Conundrum_ID,
        Panic_ID,
    )
)


@dataclass
class CastReceipt:
    skill_id: int
    target_id: int
    caster_id: int
    queued_tick: int
    dispatched_tick: int = 0
    cancelled: bool = False
    started: bool = False
    native_finished: bool = False
    hex_preexisting: bool = False
    evidence: str = "queued"
    mutation_targets: tuple[int, ...] = ()
    shatter_fragility_feeder: bool = False
    shatter_optional_feeder_skill: int = 0
    shatter_optional_application_token: int = 0
    shatter_optional_application_posted_at: int = 0
    shatter_optional_application_owner: str = ""
    shatter_consumed_hex_skill_id: int = 0
    shatter_consumed_hex_lease_posted_at: int = 0
    shatter_optional_deep_wound_before: bool = False
    shatter_optional_web_casting_before: bool = False
    queue_reject_reason: str = ""
    aneurysm_expected_gain: float = 0.0
    aneurysm_affected_neighbors: tuple[int, ...] = ()
    aneurysm_prewrack_targets: tuple[int, ...] = ()
    epidemic_plan: dict[str, object] | None = None


def _release_owned_keys(keys: tuple[int, ...], targets: tuple[int, ...]) -> None:
    """Release only this owner's specified keys, never foreign setup claims."""
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind

        email, gid = _owner_context()
        for intent in GLOBAL_CACHE.ShMem.GetAllAccounts().Intents:
            if (
                intent.Active
                and str(intent.OwnerEmail) == email
                and int(intent.IsolationGroupID) == gid
                and int(intent.KindID) == int(WhiteboardLockKind.SKILL_TARGET)
                and int(intent.SkillID) in keys
                and int(intent.TargetAgentID) in targets
            ):
                intent.reset()
    except Exception:
        pass


def _shatter_dispatch_safe(target_id: int) -> bool:
    # Shatter Delusions removes *a* Mesmer hex; neither the skill description nor
    # our effect API exposes a trustworthy removal-order selector.  A feeder
    # reservation therefore proves timing/ownership, not which of several visible
    # hexes would be removed.  New Stage 1.45 combos fail closed on ambiguity.
    if is_fragility_shatter_feeder_target(target_id):
        return _fragility_feeder_dispatch_safe(int(target_id))
    if _optional_shatter_feeder_skill(target_id) > 0:
        return True

    hexes = _shatter_known_hex_ids(target_id)
    # Outside the feeder path remain deliberately conservative: without an
    # ordered effect stack only one visible Mesmer hex is safe to convert.
    if len(hexes) != 1:
        return False
    sid = hexes[0]
    if sid == Mind_Wrack_ID:
        return _mind_wrack_shatter_allowed(target_id)
    return _shatter_salvageable_single_protected_hex(target_id, hexes) == sid


def _try_hex_mutation_lock(target_id: int) -> bool:
    """Safety-critical lock: unlike ordinary scoring leases, fail closed."""
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength,
            WhiteboardLockKind,
            WhiteboardLockMode,
            WhiteboardReentryPolicy,
        )

        email, gid = _owner_context()
        now = _now_ms()
        if not email or now <= 0:
            return False
        return (
            int(
                GLOBAL_CACHE.ShMem.PostLock(
                    email,
                    int(WhiteboardLockKind.SKILL_TARGET),
                    _HEX_MUTATION_LOCK,
                    target_id,
                    now + 4500,
                    gid,
                    int(WhiteboardLockMode.EXCLUSIVE),
                    1,
                    int(WhiteboardReentryPolicy.NON_REENTRANT),
                    int(WhiteboardClaimStrength.HARD),
                )
            )
            != -1
        )
    except Exception:
        return False


def coordinated_dispatch_options(
    skill_id: int,
    target_id: int,
    options: InterruptDispatchOptions,
    *,
    urgent: bool = False,
) -> tuple[CastReceipt, InterruptDispatchOptions]:
    """Wrap the owning queue callbacks; issuing a command is not completion."""
    receipt = CastReceipt(int(skill_id), int(target_id), int(Player.GetAgentID()), _now_ms())
    if int(skill_id or 0) == int(Shatter_Delusions_ID or 0):
        # Stage 1.54: remember whether this queue request was admitted through the
        # authoritative Fragility feeder.  Do not consume that feeder yet: the
        # ACTION queue still has to run the dispatch guard and the game still has
        # to finish the skill. Consuming here would make our own guard reject the
        # queued cast as ambiguous.
        try:
            receipt.shatter_fragility_feeder = bool(is_fragility_shatter_feeder_target(int(target_id)))
        except Exception:
            receipt.shatter_fragility_feeder = False
        if not receipt.shatter_fragility_feeder:
            try:
                app = _optional_shatter_feeder_application(int(target_id))
            except Exception:
                app = {}
            receipt.shatter_optional_feeder_skill = int(app.get("skill_id", 0) or 0)
            receipt.shatter_optional_application_token = int(app.get("token", 0) or 0)
            receipt.shatter_optional_application_posted_at = int(app.get("posted_at", 0) or 0)
            receipt.shatter_optional_application_owner = str(app.get("owner", "") or "")
            if receipt.shatter_optional_feeder_skill == int(Phantom_Pain_ID or 0):
                try:
                    receipt.shatter_optional_deep_wound_before = bool(Agent.IsDeepWounded(int(target_id)))
                except Exception:
                    receipt.shatter_optional_deep_wound_before = False
            elif receipt.shatter_optional_feeder_skill == int(Web_of_Disruption_ID or 0):
                try:
                    receipt.shatter_optional_web_casting_before = bool(Agent.IsCasting(int(target_id)))
                except Exception:
                    receipt.shatter_optional_web_casting_before = False
        try:
            known_hexes = [int(v) for v in _shatter_known_hex_ids(int(target_id)) if int(v or 0) > 0]
        except Exception:
            known_hexes = []
        if receipt.shatter_optional_feeder_skill > 0:
            receipt.shatter_consumed_hex_skill_id = int(receipt.shatter_optional_feeder_skill)
        elif len(known_hexes) == 1:
            receipt.shatter_consumed_hex_skill_id = int(known_hexes[0])
        if receipt.shatter_consumed_hex_skill_id > 0:
            receipt.shatter_consumed_hex_lease_posted_at = int(_latest_hex_lifetime_lease_posted_at(
                int(target_id), int(receipt.shatter_consumed_hex_skill_id)
            ) or 0)
    old_guard = options.get("dispatch_guard")
    old_dispatched = options.get("on_dispatched")
    old_cancelled = options.get("on_cancelled")

    def guard() -> bool:
        if (
            receipt.cancelled
            or _now_ms() - receipt.queued_tick > 800
            or int(Player.GetAgentID()) != receipt.caster_id
            or not Agent.IsAlive(receipt.caster_id)
            or not _valid_enemy(target_id)
        ):
            return False
        if math.dist(Player.GetXY(), Agent.GetXY(target_id)) > float(Range.Spellcast.value):
            return False
        if old_guard is not None and not old_guard():
            return False
        if skill_id == Aneurysm_ID:
            # Do not deliberately refill an enemy with no observation backend.
            try:
                from Py4GWCoreLib.Builds.Skills import ReforgedSupport

                ReforgedSupport.peek_local_cast_events()
            except Exception:
                receipt.evidence = "aneurysm_native_backend_unavailable"
                return False
        is_hex = skill_id in _COORDINATED_HEX_IDS
        receipt.hex_preexisting = is_hex and skill_id in _effect_ids(target_id)
        if skill_id == Shatter_Delusions_ID and not _shatter_dispatch_safe(target_id):
            receipt.evidence = "shatter_hex_ambiguous"
            return False
        if (
            skill_id == Shatter_Delusions_ID
            and int(receipt.shatter_optional_application_token or 0) > 0
            and not _optional_application_is_current(int(target_id), int(receipt.shatter_optional_application_token))
        ):
            receipt.evidence = "optional_application_changed_or_retired"
            return False
        affected = (int(target_id),)
        if skill_id in (Fragility_ID, Ineptitude_ID, Wandering_Eye_ID, Clumsiness_ID):
            affected = _fragility_packet_ids(target_id) or affected
        if skill_id == Web_of_Disruption_ID and not urgent:
            # Web itself interrupts immediately if a skill happens to be active.
            # A late damage setup must not steal a protected Mistrust/Overload cast
            # or race another already-reserved direct interrupt.
            if is_mistrust_covering_cast(target_id) or _is_overload_setup_protected(target_id) or is_interrupt_reserved(target_id):
                receipt.evidence = "web_setup_would_break_interrupt_plan"
                return False
        if is_hex and not urgent:
            # Stage 1.62: an already-reserved Web/Phantom setup is a paid
            # conversion pipeline even before native FINISHED publishes the
            # feeder. Do not let a later hex cover that target during the short
            # cross-client dispatch race. The setup skill itself is exempt.
            if skill_id not in (Web_of_Disruption_ID, Phantom_Pain_ID) and any(
                is_optional_shatter_pipeline_pending(aid) for aid in affected
            ):
                receipt.evidence = "optional_shatter_pipeline_cover_blocked"
                return False
            if skill_id == Fragility_ID:
                if any(is_optional_shatter_feeder_window(aid) for aid in affected):
                    receipt.evidence = "optional_feeder_cover_blocked"
                    return False
            elif any(is_any_shatter_feeder_window(aid) for aid in affected):
                receipt.evidence = "feeder_cover_blocked"
                return False
        if is_hex or skill_id == Shatter_Delusions_ID:
            acquired: list[int] = []
            for aid in sorted(set(affected)):
                if not _try_hex_mutation_lock(aid):
                    _release_owned_keys((_HEX_MUTATION_LOCK,), tuple(acquired))
                    receipt.evidence = "hex_mutation_busy_or_unavailable"
                    return False
                acquired.append(aid)
            receipt.mutation_targets = tuple(acquired)
            # Another client may have finished a hex while we acquired the lock.
            if skill_id == Shatter_Delusions_ID and not _shatter_dispatch_safe(target_id):
                _release_owned_keys((_HEX_MUTATION_LOCK,), receipt.mutation_targets)
                receipt.mutation_targets = ()
                return False
            if (
                skill_id == Shatter_Delusions_ID
                and int(receipt.shatter_optional_application_token or 0) > 0
                and not _optional_application_is_current(int(target_id), int(receipt.shatter_optional_application_token))
            ):
                receipt.evidence = "optional_application_changed_or_retired_after_mutex"
                _release_owned_keys((_HEX_MUTATION_LOCK,), receipt.mutation_targets)
                receipt.mutation_targets = ()
                return False
        if urgent and is_hex:
            for aid in affected:
                _try_lock(_FRAGILITY_INVALIDATED_LOCK, aid, FRAGILITY_FEEDER_WINDOW_MS)
                _try_lock(_OPTIONAL_FEEDER_INVALIDATED_LOCK, aid, max(WEB_SHATTER_FEEDER_MS, PHANTOM_SHATTER_FEEDER_MS))
        return True

    def dispatched() -> None:
        receipt.dispatched_tick = _now_ms()
        receipt.evidence = "command_dispatched"
        _record_interrupt_dispatch_delay(receipt)
        if old_dispatched is not None:
            old_dispatched()

    def cancelled() -> None:
        receipt.cancelled = True
        if receipt.evidence == "queued":
            receipt.evidence = "queue_cancelled"
        _release_owned_keys((_HEX_MUTATION_LOCK,), receipt.mutation_targets)
        if old_cancelled is not None:
            old_cancelled()

    return receipt, {"dispatch_guard": guard, "on_dispatched": dispatched, "on_cancelled": cancelled}


# Stage 1.47: ACTION-queue dispatch happens only after the current controller
# step yields back to the runtime. Waiting here for on_dispatched creates a
# circular wait (queue accepted -> controller waits -> queue cannot dispatch).
# Keep receipts process-local and reconcile them on later controller ticks.
_PENDING_CAST_RECEIPTS: list[CastReceipt] = []
_ASYNC_RECEIPT_QUEUE_TIMEOUT_MS = 1800
_ASYNC_RECEIPT_OUTCOME_TIMEOUT_MS = 4500
_ASYNC_RECEIPT_MAX = 64


def _log_async_cast_receipt(receipt: CastReceipt, *, completed: bool) -> None:
    # Avoid the Stage 1.44 per-tick log flood. Always log failures and the combo
    # skills whose observed outcome matters for later coordinated actions.
    important = int(receipt.skill_id) in {
        int(Aneurysm_ID or 0), int(Fragility_ID or 0), int(Shatter_Delusions_ID or 0),
        int(Web_of_Disruption_ID or 0), int(Phantom_Pain_ID or 0), int(Epidemic_ID or 0),
    }
    if not important and completed:
        return
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        extra = {}
        if not completed:
            # Best-effort observations, NOT a claim that movement caused the
            # failed cast. Never change reservations or cast status here.
            def _probe(name: str, fn) -> None:
                try:
                    extra[name] = fn()
                except Exception:
                    extra[name] = "unknown"
            _probe("caster_moving", lambda: bool(Agent.IsMoving(int(receipt.caster_id))))
            _probe("caster_current_skill_id", lambda: int(Agent.GetCastingSkillID(int(receipt.caster_id)) or 0))
            _probe("caster_hp_pct", lambda: round(float(Agent.GetHealth(int(receipt.caster_id))), 3))
            _probe("caster_energy_pct", lambda: round(float(Agent.GetEnergy(int(receipt.caster_id))), 3))
            _probe("target_alive", lambda: bool(Agent.IsAlive(int(receipt.target_id))))
            _probe("target_hp_pct", lambda: round(float(Agent.GetHealth(int(receipt.target_id))), 3))
            _probe("target_distance", lambda: round(float(math.dist(Agent.GetXY(int(receipt.caster_id)), Agent.GetXY(int(receipt.target_id)))), 1))
            extra["queued_tick"] = int(receipt.queued_tick or 0)
            extra["dispatched_tick"] = int(receipt.dispatched_tick or 0)
            extra["receipt_age_ms"] = max(0, int(_now_ms()) - int(receipt.queued_tick or 0))
        CombatDebug.log_event(
            "MESMERWAY_CAST_RECEIPT",
            **extra,
            skill_id=int(receipt.skill_id),
            target_id=int(receipt.target_id),
            evidence=str(receipt.evidence),
            completed=bool(completed),
            queue_reject_reason=str(receipt.queue_reject_reason or ""),
            queue_accepted=bool(int(receipt.dispatched_tick or 0) > 0 or str(receipt.evidence) != "not_queued"),
            native_started=bool(receipt.started),
            native_finished=bool(receipt.native_finished),
            effect_confirmed=bool(str(receipt.evidence).startswith("hex_effect_observed")),
            processing_terminal=bool(completed),
            outcome_observed=bool(receipt.native_finished or str(receipt.evidence) in {"stopped", "interrupted", "superseded"}),
            # Native FINISHED proves cast completion, not the target-side effect.
            # Only explicit effect-observation evidence is a direct effect proof.
            authoritative_game_effect=bool(str(receipt.evidence).startswith("hex_effect_observed")),
            model_inference_from_native_finish=bool(
                receipt.native_finished and int(receipt.skill_id or 0) == int(Aneurysm_ID or 0)
            ),
            policy="stage1_55_queue_cast_effect_states_are_distinct",
        )
    except Exception:
        pass


def _invalidate_failed_async_cast(receipt: CastReceipt) -> None:
    """Undo only safety/coordination facts that become dangerous if a cast failed."""
    aid = int(receipt.target_id or 0)
    sid = int(receipt.skill_id or 0)
    _release_owned_keys((_HEX_MUTATION_LOCK,), tuple(receipt.mutation_targets or ()))
    if sid == int(Shatter_Delusions_ID or 0):
        _release_owned_keys((_SHATTER_DELUSIONS_TARGET_LOCK,), (aid,))
    elif sid == int(Fragility_ID or 0):
        affected = tuple(receipt.mutation_targets or _LOCAL_FRAGILITY_PENDING_PACKETS.get(aid, ()) or (aid,))
        for target_id in affected:
            _try_lock(_FRAGILITY_INVALIDATED_LOCK, int(target_id), FRAGILITY_FEEDER_WINDOW_MS)
        release_fragility_reservation(aid, reason=str(receipt.evidence or "async_failed"))
    elif sid == int(Web_of_Disruption_ID or 0):
        _try_lock(_OPTIONAL_FEEDER_INVALIDATED_LOCK, aid, WEB_SHATTER_FEEDER_MS)
        release_optional_setup_reservation(sid, aid)
    elif sid == int(Phantom_Pain_ID or 0):
        _try_lock(_OPTIONAL_FEEDER_INVALIDATED_LOCK, aid, PHANTOM_SHATTER_FEEDER_MS)
        release_optional_setup_reservation(sid, aid)
    elif sid == int(Aneurysm_ID or 0):
        # Stage 1.55 publishes no refill/drain facts before native FINISHED, so a
        # failed attempt only owns its pending reservation. Do not erase older
        # confirmed anchor history from a previous cycle.
        release_aneurysm_reservation(aid, reason=str(receipt.evidence or "async_failed"))
    elif sid == int(Epidemic_ID or 0):
        plan = dict(receipt.epidemic_plan or {})
        if plan:
            release_epidemic_reservation(plan, reason=str(receipt.evidence or "async_failed"))
    elif sid == int(Energy_Surge_ID or 0):
        release_energy_surge_reservation(aid, reason=str(receipt.evidence or "async_failed"))


def _finish_async_receipt(receipt: CastReceipt, *, completed: bool, evidence: str) -> None:
    receipt.evidence = str(evidence)
    if completed:
        # Stage 1.61: release the short dispatch mutex before publishing the
        # longer hex-lifetime lease.  The shared whiteboard can be capacity
        # constrained during large packets; holding the mutation slot while
        # asking for an additional timing slot made lifetime publication fail
        # silently in live 1.60 runs.
        _release_owned_keys((_HEX_MUTATION_LOCK,), tuple(receipt.mutation_targets or ()))
        sid = int(receipt.skill_id or 0)
        if sid in _HEX_LIFETIME_LOCK_BY_SKILL and (
            bool(receipt.native_finished) or str(receipt.evidence).startswith("hex_effect_observed")
        ):
            _publish_hex_lifetime_lease(receipt)
    else:
        _invalidate_failed_async_cast(receipt)
    _log_async_cast_receipt(receipt, completed=bool(completed))


def confirm_coordinated_cast(receipt: CastReceipt, accepted: bool) -> Generator[object, None, bool]:
    """Return queue acceptance immediately and reconcile evidence later.

    Stage 1.48 treats ACTION-queue acceptance as permission for the controller to
    continue, not as proof of an effect.  Native/effect evidence is observed on
    later ticks.  Crucially, missing evidence is *unknown*, not a fabricated
    failure; only explicit stop/interrupt/cancel or an undispatched queue entry
    retracts coordination facts.
    """
    if not accepted:
        receipt.cancelled = True
        receipt.evidence = "not_queued"
        sid = int(receipt.skill_id or 0)
        aid = int(receipt.target_id or 0)
        # Queue rejection is terminal for the *attempt* and must release every
        # selector-side reservation immediately. It is not a cast failure because
        # no game command was admitted.
        _release_owned_keys((_HEX_MUTATION_LOCK,), tuple(receipt.mutation_targets or ()))
        if sid == int(Fragility_ID or 0):
            cleanup = globals().get("release_fragility_reservation")
            if callable(cleanup):
                cleanup(aid, reason=str(receipt.queue_reject_reason or "not_queued"))
        elif sid == int(Aneurysm_ID or 0):
            cleanup = globals().get("release_aneurysm_reservation")
            if callable(cleanup):
                cleanup(aid, reason=str(receipt.queue_reject_reason or "not_queued"))
        elif sid == int(Shatter_Delusions_ID or 0):
            _release_owned_keys((_SHATTER_DELUSIONS_TARGET_LOCK,), (aid,))
        elif sid in (int(Web_of_Disruption_ID or 0), int(Phantom_Pain_ID or 0)):
            cleanup = globals().get("release_optional_setup_reservation")
            if callable(cleanup):
                cleanup(sid, aid)
        elif sid == int(Epidemic_ID or 0):
            plan = dict(receipt.epidemic_plan or {})
            if plan:
                release_epidemic_reservation(plan, reason=str(receipt.queue_reject_reason or "not_queued"))
        if int(receipt.skill_id or 0) in {
            int(Aneurysm_ID or 0), int(Fragility_ID or 0), int(Shatter_Delusions_ID or 0),
            int(Web_of_Disruption_ID or 0), int(Phantom_Pain_ID or 0), int(Epidemic_ID or 0),
        }:
            _log_async_cast_receipt(receipt, completed=False)
        if False:
            yield
        return False

    receipt.evidence = "queued_async"
    if receipt not in _PENDING_CAST_RECEIPTS:
        _PENDING_CAST_RECEIPTS.append(receipt)
    if len(_PENDING_CAST_RECEIPTS) > int(_ASYNC_RECEIPT_MAX):
        stale = _PENDING_CAST_RECEIPTS.pop(0)
        # Overflow means observation was lost, not that the game rejected the cast.
        _finish_async_receipt(stale, completed=True, evidence="async_receipt_overflow_unobserved")
    if False:
        yield
    return True


def observe_pending_cast_receipts() -> None:
    """Reconcile accepted Mesmer casts without stalling or inventing failure.

    Explicit terminal failures retract optimistic coordination. A native finish
    proves cast completion. Web still requires direct effect proof; Stage 1.58
    permits Phantom native-FINISHED provenance only while zero competing Mesmer
    hexes are visible. For Fragility, Stage 1.53 treats a native
    FINISHED event as authoritative owner-side completion proof because live logs
    show the enemy-effect cache never exposes the ally-owned hex reliably.
    """
    if not _PENDING_CAST_RECEIPTS:
        return
    now = int(_now_ms() or 0)
    try:
        from Py4GWCoreLib.Builds.Skills import ReforgedSupport
        events = ReforgedSupport.peek_local_cast_events()
    except Exception:
        ReforgedSupport = None
        events = None

    keep: list[CastReceipt] = []
    for receipt in list(_PENDING_CAST_RECEIPTS):
        if receipt.cancelled:
            _finish_async_receipt(receipt, completed=False, evidence=receipt.evidence or "cancelled")
            continue
        if int(receipt.caster_id or 0) != int(Player.GetAgentID() or 0) or not Agent.IsAlive(int(receipt.caster_id or 0)):
            _finish_async_receipt(receipt, completed=False, evidence="caster_unavailable")
            continue
        if int(receipt.dispatched_tick or 0) <= 0:
            if now > 0 and now - int(receipt.queued_tick or 0) >= int(_ASYNC_RECEIPT_QUEUE_TIMEOUT_MS):
                receipt.cancelled = True
                _finish_async_receipt(receipt, completed=False, evidence="queue_dispatch_unobserved")
            else:
                keep.append(receipt)
            continue

        outcome = "pending"
        effect_observed = False
        if events is not None and ReforgedSupport is not None:
            try:
                receipt.started, outcome = ReforgedSupport.match_local_cast_outcome(
                    events,
                    int(receipt.caster_id), int(receipt.skill_id), int(receipt.target_id),
                    int(receipt.dispatched_tick), bool(receipt.started),
                )
            except Exception:
                outcome = "pending"
            try:
                effect_observed = bool(ReforgedSupport.match_local_effect_observation(
                    events, int(receipt.skill_id), int(receipt.target_id), int(receipt.dispatched_tick)
                ))
            except Exception:
                effect_observed = False

        if outcome in ("stopped", "interrupted", "superseded"):
            _finish_async_receipt(receipt, completed=False, evidence=outcome)
            continue
        if outcome == "finished":
            receipt.native_finished = True

        sid = int(receipt.skill_id or 0)
        is_hex = sid in _COORDINATED_HEX_IDS
        if is_hex:
            try:
                visible = sid in _effect_ids(int(receipt.target_id))
            except Exception:
                visible = False
            effect_confirmed = bool(visible or effect_observed)
            if effect_confirmed:
                receipt.evidence = "hex_effect_observed_async"
                if sid in (int(Web_of_Disruption_ID or 0), int(Phantom_Pain_ID or 0)):
                    _publish_optional_shatter_feeder(sid, int(receipt.target_id))
                elif sid == int(Fragility_ID or 0):
                    register_fragility_cast(
                        int(receipt.target_id),
                        packet_override=tuple(receipt.mutation_targets or ()),
                    )
                _finish_async_receipt(receipt, completed=True, evidence="hex_effect_observed_async")
                continue
            if receipt.native_finished and now > 0:
                receipt.evidence = "native_finished_hex_timing"
                elapsed_ms = now - int(receipt.dispatched_tick)
                if sid == int(Fragility_ID or 0):
                    # Stage 1.53 live fix: on this runtime the enemy-effect table
                    # never exposes ally-owned Fragility even to its caster, while
                    # native FINISHED is observed reliably.  A native-finished cast
                    # is therefore the authoritative owner-side completion proof.
                    # Publish the exact packet captured under the mutation mutex at
                    # dispatch time; explicit stopped/interrupted/cancelled outcomes
                    # have already failed above and can never reach this path.
                    if _valid_enemy(int(receipt.target_id)):
                        register_fragility_cast(
                            int(receipt.target_id),
                            packet_override=tuple(receipt.mutation_targets or ()),
                        )
                        _finish_async_receipt(
                            receipt, completed=True,
                            evidence="native_finished_fragility_authoritative_feeder",
                        )
                    else:
                        _finish_async_receipt(
                            receipt, completed=True,
                            evidence="native_finished_fragility_target_invalid_no_feeder",
                        )
                    continue
                if elapsed_ms >= 500:
                    if sid == int(Phantom_Pain_ID or 0) and _valid_enemy(int(receipt.target_id)):
                        published = bool(_publish_optional_shatter_feeder(
                            sid, int(receipt.target_id), native_finished_unobserved=True
                        ))
                        _finish_async_receipt(
                            receipt, completed=True,
                            evidence=(
                                "native_finished_phantom_authoritative_feeder"
                                if published else "native_finished_phantom_no_safe_feeder"
                            ),
                        )
                        continue
                    # Web stays strict: native FINISHED without a visible effect
                    # does not prove a safe removable Web hex.
                    _finish_async_receipt(receipt, completed=True, evidence="native_finished_hex_effect_unobserved")
                    continue
        elif receipt.native_finished:
            if sid == int(Aneurysm_ID or 0):
                register_aneurysm_cast(
                    int(receipt.target_id),
                    expected_gain=float(receipt.aneurysm_expected_gain or 0.0),
                    affected_neighbors_override=tuple(receipt.aneurysm_affected_neighbors or ()),
                    prewrack_targets_override=tuple(receipt.aneurysm_prewrack_targets or ()),
                )
                _finish_async_receipt(receipt, completed=True, evidence="native_finished_aneurysm_model_estimate")
                continue
            if sid == int(Epidemic_ID or 0):
                plan = dict(receipt.epidemic_plan or {})
                if plan:
                    register_epidemic_cast(plan)
                _finish_async_receipt(receipt, completed=True, evidence="native_finished_epidemic_recipient_observation")
                continue
            if (
                sid == int(Shatter_Delusions_ID or 0)
                and int(receipt.shatter_optional_feeder_skill or 0) <= 0
                and int(receipt.shatter_consumed_hex_skill_id or 0) > 0
                and int(receipt.shatter_consumed_hex_lease_posted_at or 0) > 0
            ):
                _invalidate_hex_lifetime_evidence(
                    int(receipt.target_id), int(receipt.shatter_consumed_hex_skill_id),
                    int(receipt.shatter_consumed_hex_lease_posted_at),
                    reason="native_finished_unambiguous_shatter_resolution",
                    removal_observed=False,
                )
            if sid == int(Shatter_Delusions_ID or 0) and bool(receipt.shatter_fragility_feeder):
                # Consume the Fragility generation only after native completion.
                # The target reservation is needed only while the cast is in flight;
                # generation consumption prevents a second feeder conversion after
                # success, so release the reservation immediately.
                register_shatter_delusions_cast(
                    int(receipt.target_id),
                    fragility_feeder=True,
                )
                _release_owned_keys((_SHATTER_DELUSIONS_TARGET_LOCK,), (int(receipt.target_id),))
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "MESMERWAY_SHATTER_DELUSIONS_CONFIRMED",
                        target_id=int(receipt.target_id),
                        fragility_feeder=True,
                        evidence="native_finished",
                        cast_finished=True,
                        hex_removal_observed=False,
                        confirmation_scope="native_cast_completion_not_hex_removal",
                        policy="stage1_55_preserve_dispatch_consume_without_fabricating_effect_proof",
                    )
                except Exception:
                    pass
            if sid == int(Shatter_Delusions_ID or 0) and int(receipt.shatter_optional_feeder_skill or 0) > 0:
                feeder_sid = int(receipt.shatter_optional_feeder_skill or 0)
                record = register_optional_combo_shatter(
                    int(receipt.target_id), feeder_sid,
                    application_token=int(receipt.shatter_optional_application_token or 0),
                    application_posted_at=int(receipt.shatter_optional_application_posted_at or 0),
                    hex_lease_posted_at=int(receipt.shatter_consumed_hex_lease_posted_at or 0),
                    web_casting_before_shatter=bool(receipt.shatter_optional_web_casting_before),
                    deep_wound_before_shatter=bool(receipt.shatter_optional_deep_wound_before),
                )
                _release_owned_keys((_SHATTER_DELUSIONS_TARGET_LOCK,), (int(receipt.target_id),))
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "MESMERWAY_OPTIONAL_COMBO_SHATTER_CONFIRMED",
                        target_id=int(receipt.target_id), feeder_skill_id=int(feeder_sid),
                        deep_wound_observed=bool(record.get("deep_wound_observed", False)),
                        deep_wound_new_after_shatter=bool(record.get("deep_wound_new_after_shatter", False)),
                        web_hex_removed_observed=bool(record.get("web_hex_removed_observed", False)),
                        application_token=int(record.get("application_token", 0) or 0),
                        application_retired=bool(record.get("application_retired", False)),
                        hex_removal_observed=bool(record.get("hex_removal_observed", False)),
                        evidence="native_finished",
                        policy="stage1_66_optional_shatter_exact_application_retirement",
                    )
                except Exception:
                    pass
                _finish_async_receipt(receipt, completed=True, evidence="native_finished_optional_shatter")
                continue
            if sid == int(Shatter_Delusions_ID or 0):
                _release_owned_keys((_SHATTER_DELUSIONS_TARGET_LOCK,), (int(receipt.target_id),))
            _finish_async_receipt(receipt, completed=True, evidence="native_finished_async")
            continue

        if now > 0 and now - int(receipt.dispatched_tick or 0) >= int(_ASYNC_RECEIPT_OUTCOME_TIMEOUT_MS):
            # Event buffers are observational. Their absence must not fabricate a
            # failure, but Aneurysm also must not become an authoritative Energy
            # refill/drain without native completion proof.
            if sid == int(Aneurysm_ID or 0):
                release_aneurysm_reservation(int(receipt.target_id), reason="outcome_unobserved")
            elif sid == int(Epidemic_ID or 0) and receipt.epidemic_plan:
                release_epidemic_reservation(dict(receipt.epidemic_plan), reason="outcome_unobserved")
            elif sid == int(Shatter_Delusions_ID or 0):
                _release_owned_keys((_SHATTER_DELUSIONS_TARGET_LOCK,), (int(receipt.target_id),))
            _finish_async_receipt(receipt, completed=True, evidence="dispatch_accepted_outcome_unobserved")
            continue
        keep.append(receipt)

    _PENDING_CAST_RECEIPTS[:] = keep


# Four target-specific eSurge reservation slots represent in-flight planned casts.
# Stage 1.32 keeps ordinary targets distributed (at most two simultaneous
# reservations on one normal target), but an active Aneurysm refill anchor may
# consume all four slots.  Each follower still sees the reservations already
# made by earlier Mesmers through projected Energy, so the 3rd/4th eSurge is
# admitted only while it still clears the normal expected-packet value gate.
_ESURGE_SLOT_LOCKS = tuple(0x4D570110 + i for i in range(3))
_ESURGE_HIT_LOCKS = tuple(0x4D570120 + i for i in range(3))
ESURGE_NORMAL_TARGET_MAX_RESERVATIONS = 2
ESURGE_PRIMARY_FOCUS_MAX_RESERVATIONS = len(_ESURGE_SLOT_LOCKS)
ANEURYSM_ANCHOR_MAX_ESURGE_RESERVATIONS = len(_ESURGE_SLOT_LOCKS)
_MISTRUST_CAST_LOCK = 0x4D570102
_MISTRUST_ACTIVE_FALLBACK_LOCK = 0x4D570103
_INTERRUPT_TARGET_LOCK = 0x4D570104
_UNNATURAL_TARGET_LOCK = 0x4D570105
_SPIRITUAL_PAIN_TARGET_LOCK = 0x4D570106
_OVERLOAD_TARGET_LOCK = 0x4D57010A
_EVAS_TARGET_LOCK = 0x4D570107
_ANEURYSM_CAST_LOCK = 0x4D570108
_ANEURYSM_REFILLED_LOCK = 0x4D570109
# Stage 1.31: one global reset transaction plus one encoded neighbour-drain event
# per affected foe.  The key itself stores the estimated loss (1..42 Energy),
# so every Mesmer can reconstruct the same short-horizon Energy model from the
# existing whiteboard without adding a second shared-memory owner.
_ANEURYSM_GLOBAL_CAST_LOCK = 0x4D570133
_ANEURYSM_GLOBAL_TARGET = 1
# Stage 2.12: two Aneurysm carriers may alternate quickly, but not on the same
# energy tick. This short shared pulse gap gives Famine victims time to regain
# at least some Energy before the next cluster reset.
_ANEURYSM_RECENT_PULSE_LOCK = 0x4D570192
# Stage 2.15: when Famine is active and an Aneurysm carrier is ready, eSurge
# Mesmers may deliberately fan in on one central battery instead of spreading
# over the packet. The shared focus lease makes the choice deterministic across
# clients; projected Energy still stops extra eSurges once the battery is empty.
_FAMINE_ESURGE_FOCUS_LOCK = 0x4D570193
_ANEURYSM_DRAIN_KEY_BASE = 0x4D570200
_ANEURYSM_DRAIN_KEY_MAX = 42
_SHATTER_ENCHANT_TARGET_LOCK = 0x4D57010B
_TECHNOBABBLE_TARGET_LOCK = 0x4D57010C
_PANIC_PACKET_LOCK = 0x4D57010D
_WISDOM_PENDING_LOCK = 0x4D57010E
_WISDOM_ACTIVE_LOCK = 0x4D57010F
# Stage 2.02 Urgoz Frost support. Winter is a global environmental spirit, so
# ST/PI carriers coordinate one pending cast and publish the newest spatial
# coverage lease instead of blindly replacing each other every controller tick.
_WINTER_PENDING_LOCK = 0x4D57018E
_WINTER_ACTIVE_LOCK = 0x4D57018F
_WINTER_PENDING_TARGET = 0x57A1
WINTER_PENDING_MS = 5000
WINTER_PRIMARY_REFRESH_LEAD_MS = 5000
WINTER_BACKUP_REFRESH_LEAD_MS = 2600
# Stage 2.05: a freshly planted Winter from either carrier gets a short spatial
# handoff shield. Shared-memory carrier positions can briefly disagree while the
# party is moving; if the caller itself is still close to the fresh spirit, do
# not replace it just because one cross-client snapshot transiently drops below
# the 4/5 carrier threshold. This specifically prevents the observed Panic -> ST
# overwrite 13.56s later at only ~826 range separation.
WINTER_FRESH_HANDOFF_MS = 20000
WINTER_FRESH_HANDOFF_RADIUS = 1800.0
# Live 2026 spirit range is 3000 gwinches; keep 250 gwinches headroom so the
# controller replants before the party actually walks out of Winter.
WINTER_COVERAGE_RADIUS = 2750.0
# Stage 2.20 shared Greater Conflagration evidence.  The old Frost hero lease
# lived only on the controller client; this shared spatial lease lets every
# account distinguish expected GC uptime from a completed, relevant ritual.
_CONFLAGRATION_ACTIVE_LOCK = 0x4D570192
CONFLAGRATION_COVERAGE_RADIUS = 2750.0
_FROST_EVIDENCE_LAST_MS = 0
FROST_EVIDENCE_LOG_INTERVAL_MS = 3000
# Stage 2.11: Famine is carried by one Mesmer/Ranger instead of Energy Surge.
# It shares the same nature-spirit placement semantics as Winter, but has one
# owner and is maintained whenever combat is imminent/active.
_FAMINE_PENDING_LOCK = 0x4D570190
_FAMINE_ACTIVE_LOCK = 0x4D570191

# Stage 2.40: three *player accounts* may carry Aegis. PostLock itself only
# appends a whiteboard row; it is not a compare-and-swap primitive, so the old
# Stage 2.38 "exclusive" lease could be won by all clients on the same frame.
# Use a two-phase election instead: every locally ready carrier posts a short
# candidacy row, all clients wait a small settle window, then deterministically
# elect the same owner by account email. Only that owner may publish the pending
# cast lease. Synthetic targets keep this lane isolated from real agent locks.
_ESURGE_AEGIS_PENDING_LOCK = 0x4D570194
_ESURGE_AEGIS_PENDING_TARGET = 0x4D57A038
_ESURGE_AEGIS_CANDIDATE_LOCK = 0x4D570195
_ESURGE_AEGIS_CANDIDATE_TARGET = 0x4D57A039
ESURGE_AEGIS_PENDING_MS = 3200
ESURGE_AEGIS_CANDIDATE_MS = 1600
ESURGE_AEGIS_ELECTION_SETTLE_MS = 240
ESURGE_AEGIS_PRECAST_MARGIN_MS = 220
_FAMINE_PENDING_TARGET = 0xFA11
FAMINE_PENDING_MS = 5000
FAMINE_REFRESH_LEAD_MS = 5000
FAMINE_COVERAGE_RADIUS = 2750.0
# Threat scan used by Greater Conflagration + Winter.
FROST_THREAT_SCAN_RADIUS = max(float(Range.Spellcast.value), float(Range.Earshot.value) * 1.6)
_CLEANUP_TARGET_LOCK = 0x4D570130
# Stage 2.44 soft-overkill cleanup lanes: several damage accounts may commit to
# the same dying primary instead of one lock forcing everyone else to scatter.
_CLEANUP_TARGET_LOCKS = (_CLEANUP_TARGET_LOCK, 0x4D570196, 0x4D570197)
CLEANUP_MAX_CONCURRENT_FINISHERS = len(_CLEANUP_TARGET_LOCKS)
ESURGE_CLEANUP_MAX_CONCURRENT = 2
_FRUSTRATION_TARGET_LOCK = 0x4D570131
# Stage 2.54: predictive caster shutdown, one shared target lease.
_DIVERSION_TARGET_LOCK = 0x4D570198
DIVERSION_TARGET_RESERVATION_MS = 4200
_OVERLOAD_SETUP_LOCK = 0x4D570132
# Stage 1.33 coordinated Mesmer-hex / Energy battery package.
_MIND_WRACK_CAST_LOCK = 0x4D570134
_MIND_WRACK_ACTIVE_FALLBACK_LOCK = 0x4D570135
_MIND_WRACK_ANCHOR_SETUP_LOCK = 0x4D570136
_SHATTER_DELUSIONS_TARGET_LOCK = 0x4D570137
_FRAGILITY_TARGET_LOCK = 0x4D570138
_SIGNET_CORRUPTION_TARGET_LOCK = 0x4D57013B
# Stage 1.63: pre-cast Aneurysm/Wrack transaction.  The Aneurysm carrier first
# publishes the intended refill anchor and up to two adjacent foes whose
# projected Energy can be driven to zero by the upcoming Aneurysm pulse. Other
# eSurge Mesmers may plant Mind Wrack on those neighbours before the reset fires.
_ANEURYSM_PRECAST_ANCHOR_LOCK = 0x4D57013C
_ANEURYSM_PREWRACK_TARGET_LOCK = 0x4D57013D
# Stage 1.45 optional Web/Phantom/Epidemic coordination. These reuse the same
# whiteboard owner and do not create a second coordination subsystem.
_WEB_TARGET_LOCK = 0x4D570182
_WEB_FEEDER_ACTIVE_LOCK = 0x4D570183
_PHANTOM_TARGET_LOCK = 0x4D570184
_PHANTOM_FEEDER_ACTIVE_LOCK = 0x4D570185
_OPTIONAL_FEEDER_INVALIDATED_LOCK = 0x4D570186
_PHANTOM_DEEP_WOUND_SOURCE_LOCK = 0x4D570187
_PHANTOM_DEEP_WOUND_EXPECTED_LOCK = 0x4D57018D
# Stage 1.66: exact optional-feeder application retirement. The target of this
# lock is a deterministic token derived from the concrete shared feeder row
# (skill, enemy, owner, PostedAtTick, ExpiresAtTick), not the enemy AgentID.
# It retires only that application after a natively finished Shatter resolution.
_OPTIONAL_FEEDER_APPLICATION_RETIRED_LOCK = 0x5A0F0001
OPTIONAL_FEEDER_APPLICATION_RETIRED_MS = 12000
HEX_LIFETIME_REMOTE_CONFIRM_GRACE_MS = 1800
# Stage 1.66: lifetime evidence invalidation uses one dynamic key per
# (skill,target). TargetAgentID stores the exact old lease PostedAtTick cutoff.
# A later application has a newer PostedAtTick and survives a delayed old Shatter
# confirmation. This is an evidence tombstone, not a claim that removal was seen.
_HEX_EVIDENCE_INVALIDATED_BASE = 0x5A100000
HEX_EVIDENCE_INVALIDATED_MIN_MS = 5000
# Stage 1.60: native-FINISHED team hex lifetime leases.  Enemy effect-detail
# visibility is incomplete in the live multibox runtime, so End-of-Life Shatter
# cannot depend on GetEffectTimeRemaining() alone.  One key per supported Mesmer
# hex keeps the cast owner's timing authoritative without inventing removal order.
_HEX_LIFETIME_LOCK_BASE = 0x4D5701A0
_HEX_LIFETIME_LOCK_BY_SKILL = {
    int(Mind_Wrack_ID or 0): _HEX_LIFETIME_LOCK_BASE + 0,
    int(Mistrust_ID or 0): _HEX_LIFETIME_LOCK_BASE + 1,
    int(Frustration_ID or 0): _HEX_LIFETIME_LOCK_BASE + 2,
    int(Ineptitude_ID or 0): _HEX_LIFETIME_LOCK_BASE + 3,
    int(Wandering_Eye_ID or 0): _HEX_LIFETIME_LOCK_BASE + 4,
    int(Clumsiness_ID or 0): _HEX_LIFETIME_LOCK_BASE + 5,
    int(Arcane_Conundrum_ID or 0): _HEX_LIFETIME_LOCK_BASE + 6,
    int(Fragility_ID or 0): _HEX_LIFETIME_LOCK_BASE + 7,
    int(Phantom_Pain_ID or 0): _HEX_LIFETIME_LOCK_BASE + 8,
    int(Web_of_Disruption_ID or 0): _HEX_LIFETIME_LOCK_BASE + 9,
    int(Overload_ID or 0): _HEX_LIFETIME_LOCK_BASE + 10,
}
_HEX_LIFETIME_SKILL_BY_LOCK = {int(v): int(k) for k, v in _HEX_LIFETIME_LOCK_BY_SKILL.items() if int(k) > 0}
# Short provenance for a natively finished Phantom whose enemy-side effect is
# not exposed by this runtime. It is usable only while zero competing Mesmer
# hexes are visible; any visible ambiguity remains fail-closed.
_PHANTOM_NATIVE_FINISHED_FEEDER_LOCK = 0x4D57018A
_EPIDEMIC_SOURCE_LOCK = 0x4D570188
_EPIDEMIC_RECIPIENT_PENDING_LOCK = 0x4D570189
_EPIDEMIC_RECIPIENT_SIGNATURE_BASE = 0x4D571000
# Stage 1.36: Fragility is a deliberate multi-target Shatter feeder.  Pending
# locks are published when the Fragility centre is reserved so other Mesmer
# controllers cannot cover the packet while the cast is in flight.  Active
# locks are published only after a successful cast and prove that Fragility was
# the newest coordinated Mesmer hex on every marked packet member.
_FRAGILITY_FEEDER_PENDING_LOCK = 0x4D570139
_FRAGILITY_FEEDER_ACTIVE_LOCK = 0x4D57013A
# Stage 1.39: efficient generation-scoped consumption. Fragility has a 5 s
# recharge and the feeder window is 4.5 s, so a 5 s time bucket uniquely
# advances for every possible new Fragility cast. Shatter checks only the
# current and immediately previous bucket (max two shared-memory lookups)
# instead of Stage 1.38's ~47-slot backwards scan.
_FRAGILITY_GENERATION_ACTIVE_BASE = 0x4D570300
_FRAGILITY_GENERATION_CONSUMED_BASE = 0x4D570308
_FRAGILITY_GENERATION_SLOTS = 8
_FRAGILITY_GENERATION_BUCKET_MS = 5000
# Per-target short-horizon evidence for the extra 1 Energy lost when the *owner*
# of Mind Wrack targets that foe with a non-hex Mesmer skill. Eight event slots
# are enough to cover several rapid Fast-Casting actions without changing ShMem.
_MIND_WRACK_DRAIN_LOCKS = tuple(0x4D570160 + i for i in range(8))

# Local observation guard for recycled AgentIDs/session changes. Shared rows remain
# the coordination source; this only rejects rows known to predate the current
# local instance/generation.
_IDENTITY_ROW_GUARD: dict[int, tuple[str, int, int]] = {}

ESURGE_TARGET_RESERVATION_MS = 3200
ESURGE_HIT_EVIDENCE_MS = 4800
MISTRUST_PENDING_MS = 1400
MISTRUST_ACTIVE_FALLBACK_MS = 6200
INTERRUPT_RESERVATION_MS = 800
# Stage 1.25 backup takeover never invents a new game timing constant. It only
# releases a foreign exact-cast claim when shared state proves the owner cannot
# currently deliver any direct interrupt (dead/KD/out of range/no ready skill).
FILLER_TARGET_RESERVATION_MS = 850
# Shared Shatter/Drain Enchantment uses one slightly longer pending window.
# Do not lock for a full effect duration: multiple enchantments can legitimately
# need repeated removal, and only confirmed state may justify further suppression.
ENCHANT_REMOVAL_PENDING_MS = 1350
EVAS_TARGET_RESERVATION_MS = 2400
ANEURYSM_RESERVATION_MS = 3500
# Keep the energy-reset evidence long enough for Fast-Casting-reduced Aneurysm
# cycles, but stop eSurge anchor focus as soon as projected Energy is exhausted.
ANEURYSM_REFILLED_MS = 6500
ANEURYSM_NEIGHBOR_DRAIN_MS = 6500
# Fallback only.  Live eSurge drain is derived from the actual Domination rank
# and Skill.GetProgressionData(), so HR/cons/runes (e.g. Domination 20 => 13)
# are reflected automatically instead of hard-coding the old rank-15 value.
ESURGE_ESTIMATED_DRAIN_FALLBACK = 10.0
# Latest Urgoz log contained 25/237 eSurges below 80 expected packet damage,
# including 9 casts at exactly 0.  Below this floor, let Cry/Overload/fillers
# take the action instead of spending the elite for less than a normal hit.
ESURGE_MIN_EXPECTED_TOTAL_DAMAGE = 80.0
# Stage 1.30M1: current PvE Energy Surge contract: 7 primary damage per Energy
# lost and 75% of that damage to each nearby foe.
ESURGE_DAMAGE_PER_ENERGY = 7.0
ESURGE_NEARBY_DAMAGE_FACTOR = 0.75
ANEURYSM_MIN_RECENT_DRAIN = 20.0
# With Famine spatially active, even a smaller refill is valuable when it can
# zero one or more adjacent foes. Keep the old threshold outside Famine.
ANEURYSM_FAMINE_MIN_RECENT_DRAIN = 5.0
ANEURYSM_MIN_TARGET_HP = 0.10
ANEURYSM_PRECAST_PLAN_MS = 5200
# Log review: the one successful cross-client Wrack handoff completed in 437 ms,
# while 6/7 strict waits aborted. Wrack is now opportunistic rather than a hard
# blocker for the Aneurysm/Famine pulse.
ANEURYSM_PREWRACK_WAIT_MS = 700
# Stage 2.25: the optional pre-Wrack transaction itself has a hard lifetime.
# If this carrier is not scheduled again until much later, stale anchor/setup
# state must not survive for the old 5.2 s plan TTL (live logs reached 9.7 s).
ANEURYSM_PREWRACK_TRANSACTION_MAX_MS = 1000
ANEURYSM_PREWRACK_MAX_TARGETS = 2
ANEURYSM_PULSE_GAP_MS = 2400
# Stage 2.15 Famine setup: keep the shared battery focus only long enough for a
# short eSurge volley plus the Aneurysm handoff. Two adjacent foes is the minimum
# packet where the setup provides a real AoE/Famine reason to concentrate.
FAMINE_ESURGE_FOCUS_MS = 5200
FAMINE_ESURGE_FOCUS_MIN_DENSITY = 2
FAMINE_ESURGE_FOCUS_MAX_RESERVATIONS = len(_ESURGE_SLOT_LOCKS)
CLEANUP_THRESHOLD = 0.10
CLEANUP_HARD_THRESHOLD = 0.05
CLEANUP_TARGET_RESERVATION_MS = 1250
FRUSTRATION_TARGET_RESERVATION_MS = 1800
MIND_WRACK_PENDING_MS = 1500
MIND_WRACK_ACTIVE_FALLBACK_MS = 2600
MIND_WRACK_DRAIN_EVIDENCE_MS = 6500
MIND_WRACK_ANCHOR_SETUP_MS = 1800
MIND_WRACK_MIN_TARGET_HP = 0.14
# Stage 1.36 log-driven Wrack filter: outside an Aneurysm reset, Wrack is setup
# only for batteries that should reach zero in roughly two eSurges and are not
# already almost empty.  This cuts the Stage 1.35 broad 174-cast spam while
# preserving the deliberate zero-Energy burst cycle.
MIND_WRACK_MAX_ESURGES_TO_ZERO = 2.15
MIND_WRACK_MIN_DRAIN_FRACTION = 0.45
SHATTER_DELUSIONS_RESERVATION_MS = 2000
SHATTER_FRAGILITY_FEEDER_RESERVATION_MS = 4750
SIGNET_CORRUPTION_TARGET_RESERVATION_MS = 1400
WEB_TARGET_RESERVATION_MS = 1100
WEB_SHATTER_FEEDER_MS = 2600
WEB_SETUP_MIN_DENSITY = 2
WEB_SETUP_MIN_SHATTER_PACKET_DAMAGE = 140.0
PHANTOM_TARGET_RESERVATION_MS = 2800
PHANTOM_SHATTER_FEEDER_MS = 3200
PHANTOM_MIN_SETUP_HP = 0.22
PHANTOM_DEEP_WOUND_SOURCE_MS = 5000
EPIDEMIC_SOURCE_MIN_HP = 0.25
EPIDEMIC_SOURCE_RESERVATION_MS = 1200
EPIDEMIC_RECIPIENT_PENDING_MS = 1200
EPIDEMIC_SERVED_SIGNATURE_MS = 4200
EPIDEMIC_MIN_NEW_RECIPIENTS = 2
EPIDEMIC_MIN_CONDITION_REMAINING_MS = 650
EPIDEMIC_CONFIRMED_PRESENT_UNKNOWN_DURATION_MS = 1200
EPIDEMIC_PHANTOM_EXPECTED_DEEP_WOUND_MS = 1600
PHANTOM_DEEP_WOUND_EXPECTED_SOURCE_MS = 5000
EPIDEMIC_PHANTOM_SOURCE_MIN_HP = 0.12
EPIDEMIC_SINGLE_IMPORTANT_SCORE = 8.0
FRAGILITY_TARGET_RESERVATION_MS = 1800
FRAGILITY_FEEDER_PENDING_MS = 1200
FRAGILITY_FEEDER_WINDOW_MS = 4500
SHATTER_SALVAGE_MISTRUST_MS = 900
SHATTER_SALVAGE_FRUSTRATION_MS = 900
SHATTER_SALVAGE_ATTACK_HEX_MS = 650
SHATTER_SALVAGE_ARCANE_MS = 900
SHATTER_SALVAGE_FRAGILITY_MS = 650
SHATTER_SALVAGE_OVERLOAD_MS = 850
# Stage 1.51 restores the Stage 1.37/1.44 authoritative cross-client feeder.
# Only the Fragility owner must confirm the effect before publication; Shatter
# consumers trust the shared lease and keep mutation/invalidation safeguards.
SHATTER_FRAGILITY_FEEDER_MIN_REMAINING_MS = 0
FRAGILITY_FEEDER_CONSUMED_MS = FRAGILITY_FEEDER_WINDOW_MS + 250
SHATTER_MIND_WRACK_FINISHER_HP = 0.18
SHATTER_MIND_WRACK_EXPIRY_MS = 2500
# Stage 1.22 synergy policy. Frustration is a soft team focus, but genuinely
# dangerous casts on other foes still override that focus. Overload receives a
# very short setup lease only on long, non-emergency activations so another
# Mesmer does not interrupt the cast before Overload has landed.
FRUSTRATION_DANGER_OVERRIDE_SCORE = 96
OVERLOAD_SETUP_LOCK_MS = 360
OVERLOAD_SETUP_MIN_REMAINING_MS = 1800
# Stage 1.23: Cry is a damage interrupt, not a packet-only reserve.
# Stage 1.24: preserve that spam behavior but make target scope strict
# cluster-first. Singles/outside targets are fallback only when the authoritative
# packet has no usable cast or Cry's AoE would break protected Mistrust/Overload
# setup casts inside that packet. Frustration remains a positive interrupt focus.

# Stage 1.20: live Urgoz HM timing gate.  The 2026-09-20 run produced 59
# measured direct-interrupt attempts.  Every confirmed success had at least
# 1281 ms of estimated cast time remaining when the command was fired, while
# most completed/late attempts were below ~1.0 s.  Multibox dispatch therefore
# needs substantially more headroom than the native single-client feasibility
# model alone.  Keep per-skill gates conservative and leave unknown timing
# fail-open so unusual/uncatalogued casts are not silently disabled.
# Stage 1.29 live retune after the 2026-09-21 Stage 1.28 Urgoz sample.
# Stage 1.26's 1900 ms normal gates suppressed every observed Power Spike and
# Complicate candidate (the sample maxed at ~1891 ms).  We therefore reopen only
# the upper live-observed timing tail instead of reverting to the old permissive
# 1500/1250 policy.  BuildMgr's native ping/activation feasibility gate still
# remains authoritative at dispatch time.  Emergency passes keep the lower floor.
# Stage 2.11: live queue observations showed the old 1.3-1.8 s gates skipping
# many interruptible casts even though local dispatch latency was typically far lower.
# Keep a healthy margin above the 1/4 s Mesmer interrupt activations without
# abandoning casts that still have about a second left.
INTERRUPT_MIN_REMAINING_COMPLICATE_MS = 1200
INTERRUPT_EMERGENCY_MIN_REMAINING_COMPLICATE_MS = 600
INTERRUPT_MIN_REMAINING_CRY_MS = 850
SPLINTER_WEAPON_INTERRUPT_MIN_REMAINING_CRY_MS = 550
SPLINTER_WEAPON_INTERRUPT_DISPATCH_MIN_REMAINING_CRY_MS = 450
INTERRUPT_MIN_REMAINING_POWER_SPIKE_MS = 1100
INTERRUPT_EMERGENCY_MIN_REMAINING_POWER_SPIKE_MS = 850
_LATE_INTERRUPT_SKIP_LOG_UNTIL: dict[tuple[int, int, int], int] = {}
_INTERRUPT_DISPATCH_DELAYS_MS: list[int] = []
_INTERRUPT_LATE_EVAL_STATE: dict[tuple[int, int, int], tuple[int, int]] = {}
_LAST_INTERRUPT_LATE_SKIP_LOG_MS = 0


def _record_interrupt_dispatch_delay(receipt: CastReceipt) -> None:
    sid = int(receipt.skill_id or 0)
    if sid not in {int(Cry_of_Frustration_ID), int(Complicate_ID), int(Power_Spike_ID), int(Power_Drain_ID), int(Web_of_Disruption_ID)}:
        return
    delay = max(0, int(receipt.dispatched_tick or 0) - int(receipt.queued_tick or 0))
    if delay <= 0:
        return
    _INTERRUPT_DISPATCH_DELAYS_MS.append(int(delay))
    if len(_INTERRUPT_DISPATCH_DELAYS_MS) > 64:
        del _INTERRUPT_DISPATCH_DELAYS_MS[:-64]


def _interrupt_dispatch_delay_stats() -> tuple[int, int, int]:
    rows = sorted(int(v) for v in _INTERRUPT_DISPATCH_DELAYS_MS if int(v) >= 0)
    if not rows:
        return 0, 0, 0
    def pct(q: float) -> int:
        idx = max(0, min(len(rows) - 1, int(round((len(rows) - 1) * q))))
        return int(rows[idx])
    return pct(0.50), pct(0.90), int(rows[-1])


# Exact cast identity reserved through DangerInterruptClaim.  The local mapping
# lets the build release the right cross-account claim if command dispatch fails
# and preserves the original enemy skill id for post-cast diagnostics.
_EXACT_INTERRUPT_RESERVED_CASTS: dict[tuple[int, int], int] = {}
_INTERRUPT_DISPATCH_TOKENS: dict[tuple[int, int], object] = {}
_PI_PROTECTED_OVERRIDE_CASTS: dict[tuple[int, int], int] = {}


@dataclass(frozen=True, slots=True)
class CryPacket:
    target_id: int
    covered_enemy_ids: tuple[int, ...]
    covered_casts: tuple[tuple[int, int], ...] = ()
    damage_enemy_ids: tuple[int, ...] = ()
    danger_score: int = 0
    frustration_focus: bool = False
    mode: str = "standard"


def _now_ms() -> int:
    try:
        import PySystem
        return int(PySystem.get_tick_count64() or 0)
    except Exception:
        return 0


def _wisdom_focus_contact_present() -> bool:
    """Best-effort authoritative packet contact, independent of IsInAggro flicker."""
    try:
        from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
        return int(TeamCombatFocus.get_team_cluster_anchor(
            filter_range=Range.Spellcast.value,
            minimum_enemies=1,
            consumer_role="mesmerway_wisdom_clock",
        ) or 0) > 0
    except Exception:
        return False


def wisdom_combat_elapsed_ms(in_aggro: bool = False) -> int:
    """Persistent local fight clock for Wisdom placement.

    Stage 1.28 reset its timer on every transient ``IsInAggro()==False`` frame,
    which kept live Urgoz decisions stuck at ``settling_after_contact``.  This
    clock starts on either direct aggro or an authoritative combat packet, keeps
    running across short contact gaps, and resets on CombatSession epoch changes
    or a sustained no-contact grace expiry.
    """
    now = _now_ms()
    cid = int(Player.GetAgentID() or 0)
    if now <= 0 or cid <= 0:
        return 0
    try:
        from Py4GWCoreLib.Builds.Skills import CombatSession
        epoch = int(CombatSession.current().epoch or 0)
    except Exception:
        epoch = 0

    state = _WISDOM_COMBAT_CLOCKS.get(cid)
    if state is None or int(state.get("epoch", -1)) != epoch:
        state = {"epoch": epoch, "started": 0, "last_contact": 0}

    packet_contact = _wisdom_focus_contact_present()
    contact = bool(in_aggro) or bool(packet_contact)
    if contact:
        if int(state.get("started", 0) or 0) <= 0:
            state["started"] = int(now)
        state["last_contact"] = int(now)
    else:
        last = int(state.get("last_contact", 0) or 0)
        if last <= 0 or int(now) - last > int(WISDOM_COMBAT_CONTACT_GRACE_MS):
            state["started"] = 0
            state["last_contact"] = 0

    _WISDOM_COMBAT_CLOCKS[cid] = state
    started = int(state.get("started", 0) or 0)
    return max(0, int(now) - started) if started > 0 else 0


def _owner_context() -> tuple[str, int]:
    try:
        email = str(Player.GetAccountEmail() or "").strip()
    except Exception:
        return "", 0
    if not email:
        return "", 0
    gid = 0
    try:
        gid = int(GLOBAL_CACHE.ShMem.GetAccountGroupByEmail(email) or 0)
    except Exception:
        gid = 0
    if gid <= 0:
        try:
            gid = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
        except Exception:
            gid = 0
    return email, max(0, gid)


def _is_locked(lock_id: int, target_id: int) -> bool:
    if int(target_id or 0) <= 0:
        return False
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode,
            WhiteboardReentryPolicy,
        )
        email, gid = _owner_context()
        now = _now_ms()
        if not email or now <= 0:
            return False
        return bool(GLOBAL_CACHE.ShMem.IsLockBlocked(
            int(WhiteboardLockKind.SKILL_TARGET), int(lock_id), int(target_id),
            int(gid), email, int(now), int(WhiteboardLockMode.EXCLUSIVE), 1,
            int(WhiteboardReentryPolicy.NON_REENTRANT),
            int(WhiteboardClaimStrength.HARD),
        ))
    except Exception:
        return False


def _try_lock(lock_id: int, target_id: int, duration_ms: int) -> bool:
    """Atomically try to reserve a target. Fail-open without shared memory."""
    if int(target_id or 0) <= 0:
        return False
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode,
            WhiteboardReentryPolicy,
        )
        email, gid = _owner_context()
        now = _now_ms()
        if not email or now <= 0:
            return True
        slot = GLOBAL_CACHE.ShMem.PostLock(
            email, int(WhiteboardLockKind.SKILL_TARGET), int(lock_id), int(target_id),
            int(now) + int(duration_ms), int(gid), int(WhiteboardLockMode.EXCLUSIVE),
            1, int(WhiteboardReentryPolicy.NON_REENTRANT),
            int(WhiteboardClaimStrength.HARD),
        )
        return int(slot) != -1
    except Exception:
        return True


def _valid_enemy(agent_id: int) -> bool:
    try:
        return int(agent_id or 0) > 0 and Agent.IsValid(int(agent_id)) and Agent.IsAlive(int(agent_id))
    except Exception:
        return False


def _hex_enemy_invalid_reason(agent_id: int) -> str:
    """Return the exact BuildMgr-parity reason an enemy cannot receive a Hex."""
    aid = int(agent_id or 0)
    if aid <= 0:
        return "invalid_id"
    try:
        if not Agent.IsValid(aid):
            return "agent_invalid"
        if not Agent.IsAlive(aid):
            return "dead"
        if Agent.IsSpirit(aid):
            return "spirit"
        # Stage 1.58 live proof: Agent.IsSpawned() is not a generic
        # summoned-enemy predicate. Ordinary PvE enemies can report it true.
        # Agent.IsSpirit() already performs the allegiance-aware spirit test.
    except Exception:
        return "agent_query_error"
    return ""


def _valid_hex_enemy(agent_id: int) -> bool:
    """Target parity with BuildMgr's enemy-Hex safety gate."""
    return not bool(_hex_enemy_invalid_reason(int(agent_id or 0)))


def _spellcast_reachable(agent_id: int) -> bool:
    aid = int(agent_id or 0)
    if not _valid_enemy(aid):
        return False
    try:
        return Utils.Distance(Player.GetXY(), Agent.GetXY(aid)) <= float(Range.Spellcast.value)
    except Exception:
        return False


def _team_primary_focus_target(*, consumer_role: str = "mesmerway") -> int:
    """Return the shared primary kill target when it is locally reachable."""
    try:
        from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
        return int(TeamCombatFocus.get_team_primary_target(
            filter_range=Range.Spellcast.value, minimum_enemies=1,
            consumer_role=str(consumer_role or "mesmerway_primary"),
        ) or 0)
    except Exception:
        return 0


def cluster_anchor_and_members(*, consumer_role: str = "mesmerway") -> tuple[int, list[int]]:
    """Return one *actual Adjacent packet* around the shared kill focus.

    Older stages exposed every reachable member of a transitive connected fight.
    That let different Mesmers choose different sub-clusters even though they had
    read the same TeamCombatFocus version. Stage 2.44 keeps the broad connected
    component inside TeamCombatFocus for encounter identity, but damage consumers
    receive only one Adjacent packet that contains the shared primary focus.
    """
    try:
        from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
        plan = TeamCombatFocus.get_team_combat_plan(
            filter_range=Range.Spellcast.value, minimum_enemies=2,
            consumer_role=str(consumer_role or "mesmerway"),
        )
        reachable = [int(aid) for aid in tuple(plan.member_ids or ()) if _spellcast_reachable(int(aid))]
        if not reachable:
            return 0, []
        focus = int(plan.focus_target or 0)
        if focus not in reachable:
            focus = 0
        aoe = int(plan.aoe_anchor or 0)
        if aoe not in reachable:
            aoe = 0

        # Preserve the denser AoE center only when it still covers the primary.
        center = int(focus or aoe or plan.group_anchor or reachable[0])
        if focus > 0 and aoe > 0:
            try:
                if float(Utils.Distance(Agent.GetXY(focus), Agent.GetXY(aoe))) <= float(Range.Adjacent.value):
                    center = int(aoe)
            except Exception:
                center = int(focus)

        members = []
        for aid in reachable:
            try:
                if float(Utils.Distance(Agent.GetXY(center), Agent.GetXY(aid))) <= float(Range.Adjacent.value):
                    members.append(int(aid))
            except Exception:
                continue
        if focus > 0 and focus not in members:
            members.append(int(focus))
        if center > 0 and center not in members and center in reachable:
            members.append(int(center))
        if not members:
            members = [int(focus or center or reachable[0])]
        return int(center), sorted(set(int(v) for v in members))
    except Exception:
        return 0, []


# Stage 1.28 Wisdom coordination -------------------------------------------------
#
# The old Stage 1.17 policy used one global active bit plus fixed elapsed-time
# thresholds.  That was enough to prevent most simultaneous casts, but it could
# not answer the questions that matter for a ground ward: where the ward is,
# which Mesmers are actually inside it, whether the team moved, which equipped
# carrier is best placed, or whether a second spatially distinct ward adds
# coverage.  Stage 1.28 keeps the same whiteboard architecture and replaces only
# the Wisdom-specific policy.
#
# Local Reforged data and current GWW agree on the mechanics used here:
# - 10 Energy, 1 s activation, 20 s recharge;
# - Ward Spell planted at the caster's current position, Area radius;
# - Ebon Vanguard rank gives 14..20 s duration and 44..60% HSR chance;
# - only spells benefit (not Signets / Binding Rituals);
# - HSR modifies recharge of spells cast while covered, not already-running
#   recharge timers.
# Same-Wisdom overlap is deliberately NOT treated as additive because that exact
# same-source stacking behaviour is not exposed reliably by the API.  A second
# ward is therefore justified only by refresh timing or additional spatial
# coverage, never by a presumed double proc chance.
WISDOM_MIN_COMBAT_MS = 900
# Short IsInAggro()/focus gaps occur while a real fight is still continuous.
# Keep the Wisdom combat clock alive across those gaps; reset on a real session
# transition or only after a sustained no-contact period.
WISDOM_COMBAT_CONTACT_GRACE_MS = 6500
WISDOM_RELAX_TO_TWO_MESMERS_MS = 4500
WISDOM_DEFAULT_REQUIRED_MESMERS = 3
WISDOM_LONG_FIGHT_REQUIRED_MESMERS = 2
WISDOM_PENDING_MS = 2200
WISDOM_POSITION_STEP = 32.0
WISDOM_POSITION_OFFSET = 4096
WISDOM_POSITION_BITS = 13
WISDOM_POSITION_MASK = (1 << WISDOM_POSITION_BITS) - 1
WISDOM_RELOCATE_DISTANCE = float(Range.Area.value) * 0.75
WISDOM_SECOND_GROUP_MIN_NEW_MESMERS = 2
WISDOM_RENEW_LEAD_MS = 1400  # base 1 s activation + conservative dispatch margin
WISDOM_STABLE_MARGIN = float(Range.Area.value) * 0.92
_WISDOM_PENDING_TARGET = 0x4D57F001
_WISDOM_OBS_LAST_CAST: dict[int, int] = {}
_WISDOM_LOADOUT_SIGNATURE = "__uninitialized__"
_WISDOM_PLAN_LOG_STATE: dict[int, tuple[str, int]] = {}
_WISDOM_COMBAT_CLOCKS: dict[int, dict[str, int]] = {}

# Current Ebon Vanguard title progression from local Reforged skill data.
_WISDOM_DURATION_S = (14, 15, 16, 18, 19, 20, 20, 20, 20, 20, 20)
_WISDOM_HSR_PCT = (44, 47, 50, 54, 57, 60, 60, 60, 60, 60, 60)
_EBON_VANGUARD_POINTS = (1000, 4000, 8000, 16000, 26000, 40000, 56000, 80000, 110000, 160000)


def _wisdom_rank_from_points(points: int) -> int:
    p = max(0, int(points or 0))
    rank = 0
    for threshold in _EBON_VANGUARD_POINTS:
        if p >= int(threshold):
            rank += 1
        else:
            break
    return max(0, min(10, int(rank)))


def _wisdom_rank_for_account(account) -> int:
    try:
        # TitleID.Ebon_Vanguard == 40 in the current project enum.
        points = int(account.TitlesData.Titles[40].CurrentPoints or 0)
        return _wisdom_rank_from_points(points)
    except Exception:
        return 0


def wisdom_local_rank() -> int:
    try:
        from Py4GWCoreLib.enums_src.Title_enums import TitleID
        title = Player.GetTitle(int(TitleID.Ebon_Vanguard))
        return _wisdom_rank_from_points(int(getattr(title, "current_points", 0) or 0))
    except Exception:
        return 0


def wisdom_duration_ms(rank: int | None = None) -> int:
    r = wisdom_local_rank() if rank is None else max(0, min(10, int(rank)))
    return int(_WISDOM_DURATION_S[r]) * 1000


def wisdom_hsr_chance_pct(rank: int | None = None) -> int:
    r = wisdom_local_rank() if rank is None else max(0, min(10, int(rank)))
    return int(_WISDOM_HSR_PCT[r])


def _wisdom_pack_xy(x: float, y: float) -> int:
    xq = int(round(float(x) / WISDOM_POSITION_STEP)) + WISDOM_POSITION_OFFSET
    yq = int(round(float(y) / WISDOM_POSITION_STEP)) + WISDOM_POSITION_OFFSET
    xq = max(0, min(WISDOM_POSITION_MASK, xq))
    yq = max(0, min(WISDOM_POSITION_MASK, yq))
    return int((xq << WISDOM_POSITION_BITS) | yq) + 1


def _wisdom_unpack_xy(value: int) -> tuple[float, float]:
    raw = max(0, int(value or 0) - 1)
    yq = raw & WISDOM_POSITION_MASK
    xq = (raw >> WISDOM_POSITION_BITS) & WISDOM_POSITION_MASK
    return (
        float(xq - WISDOM_POSITION_OFFSET) * WISDOM_POSITION_STEP,
        float(yq - WISDOM_POSITION_OFFSET) * WISDOM_POSITION_STEP,
    )


def frost_threat_profile() -> dict[str, int | bool]:
    """Nearby physical/elemental threat counts for GC + Winter decisions."""
    physical = 0
    elemental = 0
    total = 0
    martial = {
        int(getattr(Profession.Warrior, "value", Profession.Warrior)),
        int(getattr(Profession.Ranger, "value", Profession.Ranger)),
        int(getattr(Profession.Assassin, "value", Profession.Assassin)),
        int(getattr(Profession.Dervish, "value", Profession.Dervish)),
        int(getattr(Profession.Paragon, "value", Profession.Paragon)),
    }
    elementalist = int(getattr(Profession.Elementalist, "value", Profession.Elementalist))
    try:
        player_xy = Player.GetXY()
        enemies = list(AgentArray.GetEnemyArray() or [])
    except Exception:
        return {"total": 0, "physical": 0, "elemental": 0, "frost_relevant": False}
    for enemy_id in enemies:
        aid = int(enemy_id or 0)
        if aid <= 0:
            continue
        try:
            if not Agent.IsAlive(aid):
                continue
            if float(Utils.Distance(player_xy, Agent.GetXY(aid))) > float(FROST_THREAT_SCAN_RADIUS):
                continue
        except Exception:
            continue
        total += 1
        p = sec = 0
        try:
            primary, secondary = Agent.GetProfessions(aid)
            p = int(getattr(primary, "value", primary) or 0)
            sec = int(getattr(secondary, "value", secondary) or 0)
        except Exception:
            pass
        if p in martial or sec in martial:
            physical += 1
        else:
            try:
                if Agent.IsAttacking(aid) and (Agent.IsMelee(aid) or Agent.IsRanged(aid)):
                    physical += 1
            except Exception:
                pass
        if p == elementalist or sec == elementalist:
            elemental += 1
    return {
        "total": int(total), "physical": int(physical), "elemental": int(elemental),
        "frost_relevant": bool(physical > 0 or elemental > 0),
    }


def greater_conflagration_should_cast() -> bool:
    return int(frost_threat_profile().get("physical", 0) or 0) > 0


def winter_environment_should_cast() -> bool:
    profile = frost_threat_profile()
    return bool(int(profile.get("physical", 0) or 0) > 0 or int(profile.get("elemental", 0) or 0) > 0)


def _winter_pack_spatial_key(x: float, y: float, now_ms: int) -> int:
    """Encode Winter cast position plus a tiny generation so refresh can coexist.

    A new Winter may be planted shortly before the old lease expires.  Reusing
    the exact same whiteboard target would make the active lease look occupied,
    so five high bits carry a rolling generation while the lower 26 bits retain
    the same Wisdom-style quantised XY payload.
    """
    spatial_raw = max(0, int(_wisdom_pack_xy(float(x), float(y))) - 1)
    payload_bits = int(WISDOM_POSITION_BITS * 2)
    payload_mask = (1 << payload_bits) - 1
    generation = (max(0, int(now_ms)) // 1000) & 0x1F
    return int((generation << payload_bits) | (spatial_raw & payload_mask)) + 1


def _winter_unpack_spatial_key(value: int) -> tuple[float, float]:
    payload_bits = int(WISDOM_POSITION_BITS * 2)
    payload_mask = (1 << payload_bits) - 1
    raw = max(0, int(value or 0) - 1) & payload_mask
    return _wisdom_unpack_xy(int(raw) + 1)


def register_greater_conflagration_cast(
    *, duration_ms: int, cast_xy: tuple[float, float], source: str = "unknown"
) -> dict[str, object]:
    """Publish only a completed GC spirit's expected spatial lifetime."""
    try:
        xy = (float(cast_xy[0]), float(cast_xy[1]))
    except Exception:
        return {}
    now = int(_now_ms() or 0)
    duration = max(1000, int(duration_ms or 0))
    key = int(_winter_pack_spatial_key(xy[0], xy[1], now))
    posted = bool(_try_lock(_CONFLAGRATION_ACTIVE_LOCK, key, duration))
    record = {
        "posted": bool(posted), "xy": xy, "duration_ms": int(duration),
        "spatial_key": int(key),
        "remaining_ms": int(duration), "source": str(source or "unknown"),
    }
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_GREATER_CONFLAGRATION_ACTIVE", posted=bool(posted),
            x=round(float(xy[0]), 1), y=round(float(xy[1]), 1),
            duration_ms=int(duration), source=str(source or "unknown"),
            confirmation_scope="hero_recharge_after_activation",
            policy="stage2_20_completed_spatial_gc_evidence",
        )
    except Exception:
        pass
    return record


def release_greater_conflagration_cast(spatial_key: int) -> None:
    """Revoke only this owner's exact observed nature-spirit registration."""
    _release_owned_keys((_CONFLAGRATION_ACTIVE_LOCK,), (int(spatial_key),))


def greater_conflagration_active_state() -> dict[str, object]:
    now = int(_now_ms() or 0)
    rows = _active_skill_target_lock_events((_CONFLAGRATION_ACTIVE_LOCK,))
    if now <= 0 or not rows:
        return {"active": False, "remaining_ms": 0, "xy": None, "physical_threats_in_range": 0}
    row = max(rows, key=lambda item: int(item.get("posted_at", 0) or 0))
    xy = _winter_unpack_spatial_key(int(row.get("target_id", 0) or 0))
    remaining = max(0, int(row.get("expires_at", 0) or 0) - now)
    threats = 0
    try:
        for enemy_id in AgentArray.GetEnemyArray() or []:
            aid = int(enemy_id or 0)
            if aid <= 0 or not Agent.IsAlive(aid):
                continue
            if float(Utils.Distance(Agent.GetXY(aid), xy)) > float(CONFLAGRATION_COVERAGE_RADIUS):
                continue
            try:
                if Agent.IsAttacking(aid) and (Agent.IsMelee(aid) or Agent.IsRanged(aid)):
                    threats += 1
            except Exception:
                pass
    except Exception:
        pass
    return {
        "active": bool(remaining > 0), "remaining_ms": int(remaining), "xy": xy,
        "physical_threats_in_range": int(threats),
        "owner": str(row.get("owner", "") or ""),
    }


def frost_combo_evidence_state() -> dict[str, object]:
    """Observed spatial Frost-chain evidence, never a damage-conversion claim."""
    winter = winter_active_state()
    gc = greater_conflagration_active_state()
    mantra_total = mantra_active = mantra_winter_covered = 0
    try:
        accounts = _wisdom_same_instance_accounts()
    except Exception:
        accounts = []
    winter_xy = winter.get("xy")
    for acc in accounts:
        try:
            ids = set(_wisdom_skill_ids(acc))
            if int(Mantra_of_Frost_ID or 0) not in ids:
                continue
            aid = int(getattr(acc.AgentData, "AgentID", 0) or 0)
            if aid <= 0 or not _wisdom_agent_alive(aid, acc):
                continue
            mantra_total += 1
            email = str(getattr(acc, "AccountEmail", "") or "")
            active = bool(
                email
                and GLOBAL_CACHE.ShMem.GetAllAccounts().AccountHasEffect(
                    email, int(Mantra_of_Frost_ID)
                )
            )
            if active:
                mantra_active += 1
                pos = _wisdom_xy(aid, acc)
                if (
                    pos is not None
                    and winter_xy is not None
                    and float(Utils.Distance(pos, winter_xy)) <= float(WINTER_COVERAGE_RADIUS)
                ):
                    mantra_winter_covered += 1
        except Exception:
            continue
    chain_ready = bool(
        gc.get("active", False)
        and int(gc.get("physical_threats_in_range", 0) or 0) > 0
        and winter.get("active", False)
        and winter.get("covered", False)
        and mantra_active > 0
        and mantra_winter_covered == mantra_active
    )
    return {
        "gc_active": bool(gc.get("active", False)),
        "gc_remaining_ms": int(gc.get("remaining_ms", 0) or 0),
        "gc_physical_threats_in_range": int(gc.get("physical_threats_in_range", 0) or 0),
        "winter_active": bool(winter.get("active", False)),
        "winter_covered": bool(winter.get("covered", False)),
        "winter_remaining_ms": int(winter.get("remaining_ms", 0) or 0),
        "mantra_carriers": int(mantra_total),
        "mantra_active": int(mantra_active),
        "mantra_active_and_winter_covered": int(mantra_winter_covered),
        "chain_spatially_ready": bool(chain_ready),
        "damage_conversion_verified": False,
    }


def log_frost_combo_evidence(*, source: str = "runtime") -> dict[str, object]:
    global _FROST_EVIDENCE_LAST_MS
    now = int(_now_ms() or 0)
    state = frost_combo_evidence_state()
    if now > 0 and now - int(_FROST_EVIDENCE_LAST_MS or 0) >= int(FROST_EVIDENCE_LOG_INTERVAL_MS):
        _FROST_EVIDENCE_LAST_MS = int(now)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_FROST_COMBO_EVIDENCE",
                source=str(source or "runtime"),
                **state,
                policy="stage2_20_spirit_position_plus_actual_mantra_evidence",
            )
        except Exception:
            pass
    return state


def winter_local_duration_ms() -> int:
    """Expected local Winter lifespan from live Wilderness Survival rank.

    PvE Winter progresses linearly: 30 seconds at rank 0, +14 seconds/rank.
    The rank helper includes live HR/cons modifiers when the client exposes them.
    """
    rank = max(0, int(_current_skill_attribute_level(Winter_ID) or 0))
    return max(30000, int(30 + 14 * rank) * 1000)


def _winter_mantra_carrier_coverage(xy: tuple[float, float]) -> dict:
    """Coverage of the living Mesmers that actually carry Mantra of Frost.

    Stage 2.03 deliberately evaluates the protected damage core rather than the
    Winter caster itself.  This prevents an offset ST/backup Mesmer from
    replacing a perfectly useful Winter while all but at most one of the Mantra
    carriers are still inside its effect.  Shared-memory positions are used as a
    fallback when another client's Agent is not locally addressable.
    """
    total = 0
    covered = 0
    members: list[int] = []
    covered_members: list[int] = []
    try:
        accounts = _wisdom_same_instance_accounts()
    except Exception:
        accounts = []
    for acc in accounts:
        try:
            if int(Mantra_of_Frost_ID) not in set(_wisdom_skill_ids(acc)):
                continue
            aid = int(getattr(acc.AgentData, "AgentID", 0) or 0)
            if aid <= 0 or not _wisdom_agent_alive(aid, acc):
                continue
            pos = _wisdom_xy(aid, acc)
            if pos is None:
                continue
            total += 1
            members.append(aid)
            if float(Utils.Distance(pos, xy)) <= float(WINTER_COVERAGE_RADIUS):
                covered += 1
                covered_members.append(aid)
        except Exception:
            continue
    required = max(1, total - 1) if total > 0 else 0
    return {
        "carrier_total": int(total),
        "carrier_covered": int(covered),
        "carrier_required": int(required),
        "carrier_members": tuple(members),
        "carrier_covered_members": tuple(covered_members),
        "majority_covered": bool(total > 0 and covered >= required),
    }


def winter_active_state() -> dict:
    """Newest expected Winter record plus party-centric Mantra coverage."""
    now = int(_now_ms() or 0)
    rows = _active_skill_target_lock_events((_WINTER_ACTIVE_LOCK,))
    if now <= 0 or not rows:
        return {
            "active": False, "remaining_ms": 0, "xy": None, "covered": False, "owner": "",
            "carrier_total": 0, "carrier_covered": 0, "carrier_required": 0,
        }
    row = max(rows, key=lambda item: int(item.get("posted_at", 0) or 0))
    xy = _winter_unpack_spatial_key(int(row.get("target_id", 0) or 0))
    remaining = max(0, int(row.get("expires_at", 0) or 0) - now)
    local_covered = False
    try:
        local_covered = bool(Utils.Distance(Player.GetXY(), xy) <= float(WINTER_COVERAGE_RADIUS))
    except Exception:
        local_covered = False
    coverage = _winter_mantra_carrier_coverage(xy)
    carrier_total = int(coverage.get("carrier_total", 0) or 0)
    # When Mantra carriers are visible in SharedMemory they define whether the
    # environmental defense is still useful.  On startup/zoning, fail back to
    # the local-player check until the shared party snapshot is populated.
    covered = bool(coverage.get("majority_covered", False)) if carrier_total > 0 else bool(local_covered)
    return {
        "active": bool(remaining > 0),
        "remaining_ms": int(remaining),
        "xy": xy,
        "covered": bool(covered),
        "local_covered": bool(local_covered),
        "owner": str(row.get("owner", "") or ""),
        "posted_at": int(row.get("posted_at", 0) or 0),
        **coverage,
    }


def _winter_primary_carrier_ready() -> bool:
    """Whether a live ST carrier can plant Winter now. Backup PI yields to it."""
    try:
        for acc in _wisdom_same_instance_accounts():
            ids = set(_wisdom_skill_ids(acc))
            if int(Soul_Twisting_ID) not in ids or int(Winter_ID) not in ids:
                continue
            aid = int(getattr(acc.AgentData, "AgentID", 0) or 0)
            if aid <= 0 or not _wisdom_agent_alive(aid, acc) or _wisdom_agent_knocked_down(aid):
                continue
            row = _wisdom_skill_row(acc, int(Winter_ID))
            if row is not None and float(getattr(row, "Recharge", 0.0) or 0.0) > 0.0:
                continue
            casting = int(getattr(acc.AgentData.Skillbar, "CastingSkillID", 0) or 0)
            if casting not in (0, int(Winter_ID)):
                continue
            return True
    except Exception:
        return False
    return False


def winter_should_cast(role: str = "backup") -> bool:
    """Spatial/expiry policy for ST-primary plus Mesmer backup Winter carriers."""
    if _is_locked(_WINTER_PENDING_LOCK, _WINTER_PENDING_TARGET):
        return False
    # Stage 2.11: cast only when Winter can actually feed Mantra of Frost: either
    # direct elemental pressure, or physical pressure that GC converts to Fire.
    if not winter_environment_should_cast():
        return False
    state = winter_active_state()
    try:
        log_frost_combo_evidence(source=f"winter_should_cast:{str(role or 'backup')}")
    except Exception:
        pass
    normalized = str(role or "backup").strip().casefold()
    if normalized != "primary" and _winter_primary_carrier_ready():
        return False

    # Stage 2.05 fresh-handoff guard. A newly planted Winter should not be
    # immediately replaced by the other carrier because one SharedMemory party
    # position arrived a tick late. The guard is deliberately spatial: once the
    # current caller has genuinely moved away from the old spirit it no longer
    # applies, so real coverage loss still replants immediately.
    if bool(state.get("active", False)) and state.get("xy") is not None:
        now = int(_now_ms() or 0)
        age = max(0, now - int(state.get("posted_at", 0) or 0)) if now > 0 else 0
        if age <= int(WINTER_FRESH_HANDOFF_MS):
            try:
                local_distance = float(Utils.Distance(Player.GetXY(), state.get("xy")))
            except Exception:
                local_distance = 999999.0
            if local_distance <= float(WINTER_FRESH_HANDOFF_RADIUS):
                if not bool(state.get("covered", False)):
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_event(
                            "MESMERWAY_WINTER_FRESH_HANDOFF_HELD",
                            role=str(normalized), age_ms=int(age),
                            local_distance=round(float(local_distance), 1),
                            carrier_total=int(state.get("carrier_total", 0) or 0),
                            carrier_covered=int(state.get("carrier_covered", 0) or 0),
                            carrier_required=int(state.get("carrier_required", 0) or 0),
                            policy="stage2_05_spatial_fresh_handoff_guard",
                        )
                    except Exception:
                        pass
                return False

    if not bool(state.get("active", False)) or not bool(state.get("covered", False)):
        return True
    lead = WINTER_PRIMARY_REFRESH_LEAD_MS if normalized == "primary" else WINTER_BACKUP_REFRESH_LEAD_MS
    return int(state.get("remaining_ms", 0) or 0) <= int(lead)


def reserve_winter_cast() -> bool:
    if _is_locked(_WINTER_PENDING_LOCK, _WINTER_PENDING_TARGET):
        return False
    return bool(_try_lock(_WINTER_PENDING_LOCK, _WINTER_PENDING_TARGET, WINTER_PENDING_MS))


def release_winter_pending(reason: str = "") -> int:
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        if not email:
            return 0
        cleared = int(GLOBAL_CACHE.ShMem.GetAllAccounts().ClearLockByOwnerKindTarget(
            email, int(WhiteboardLockKind.SKILL_TARGET), int(_WINTER_PENDING_TARGET), int(gid)
        ) or 0)
        if cleared:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event("MESMERWAY_WINTER_PENDING_RELEASE", reason=str(reason or "unspecified"), cleared=int(cleared))
            except Exception:
                pass
        return cleared
    except Exception:
        return 0


def register_winter_cast(
    *, role: str, duration_ms: int | None = None, cast_xy: tuple[float, float] | None = None
) -> dict:
    """Publish expected spatial Winter coverage after CastSkillID returned success."""
    actual_duration = max(30000, int(duration_ms if duration_ms is not None else winter_local_duration_ms()))
    try:
        raw_xy = cast_xy if cast_xy is not None else Player.GetXY()
        xy = (float(raw_xy[0]), float(raw_xy[1]))
    except Exception:
        release_winter_pending("register_no_position")
        return {}
    now = int(_now_ms() or 0)
    key = int(_winter_pack_spatial_key(xy[0], xy[1], now))
    release_winter_pending("cast_registered")
    posted = bool(_try_lock(_WINTER_ACTIVE_LOCK, key, actual_duration))
    coverage = _winter_mantra_carrier_coverage(xy)
    record = {
        "posted": bool(posted),
        "xy": xy,
        "spatial_key": int(key),
        "role": str(role or "unknown"),
        "duration_ms": int(actual_duration),
        "started_at": int(now),
        "expected_end": int(now + actual_duration) if now > 0 else 0,
        "carrier_total": int(coverage.get("carrier_total", 0) or 0),
        "carrier_covered": int(coverage.get("carrier_covered", 0) or 0),
        "carrier_required": int(coverage.get("carrier_required", 0) or 0),
    }
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_WINTER_ACTIVE",
            role=str(record["role"]), duration_ms=int(actual_duration), posted=bool(posted),
            x=round(float(xy[0]), 1), y=round(float(xy[1]), 1),
            carrier_total=int(record["carrier_total"]),
            carrier_covered=int(record["carrier_covered"]),
            carrier_required=int(record["carrier_required"]),
            policy="stage2_05_party_mantra_coverage_fresh_handoff_guard",
        )
    except Exception:
        pass
    return record


def _wisdom_skill_ids(account) -> tuple[int, ...]:
    try:
        return tuple(int(getattr(skill, "Id", 0) or 0) for skill in account.AgentData.Skillbar.Skills)
    except Exception:
        return ()


def _wisdom_skill_row(account, skill_id: int):
    sid = int(skill_id or 0)
    try:
        for row in account.AgentData.Skillbar.Skills:
            if int(getattr(row, "Id", 0) or 0) == sid:
                return row
    except Exception:
        pass
    return None


def _wisdom_is_spell_skill(skill_id: int) -> bool:
    """True only for skill types that Guild Wars treats as spells.

    Ritual (Binding Ritual), Signet, Chant, Shout, Attack, etc. are intentionally
    excluded.  Weapon Spells and Item Spells are spell families and may benefit.
    """
    sid = int(skill_id or 0)
    if sid <= 0:
        return False
    try:
        from Py4GWCoreLib.enums_src.GameData_enums import SkillType
        kind = int(Skill.GetType(sid)[0] or 0)
        return kind in {
            int(SkillType.Hex), int(SkillType.Spell), int(SkillType.Enchantment),
            int(SkillType.Well), int(SkillType.Ward), int(SkillType.ItemSpell),
            int(SkillType.WeaponSpell),
        }
    except Exception:
        return False


def _wisdom_energy_cost(skill_id: int) -> int:
    try:
        return max(0, int(Skill.Data.GetEnergyCost(int(skill_id)) or 0))
    except Exception:
        return 0


def _wisdom_xy(agent_id: int, account=None) -> tuple[float, float] | None:
    aid = int(agent_id or 0)
    if aid > 0:
        try:
            if Agent.IsValid(aid):
                xy = Agent.GetXY(aid)
                return (float(xy[0]), float(xy[1]))
        except Exception:
            pass
    if account is not None:
        try:
            pos = account.AgentData.Pos
            return (float(pos.x), float(pos.y))
        except Exception:
            pass
    return None


def _wisdom_agent_alive(agent_id: int, account=None) -> bool:
    aid = int(agent_id or 0)
    if aid > 0:
        try:
            if Agent.IsValid(aid):
                return bool(Agent.IsAlive(aid))
        except Exception:
            pass
    try:
        return float(account.AgentData.Health.Current) > 0.0
    except Exception:
        return False


def _wisdom_agent_moving(agent_id: int) -> bool:
    aid = int(agent_id or 0)
    if aid <= 0:
        return False
    try:
        return bool(Agent.IsMoving(aid))
    except Exception:
        return False


def _wisdom_agent_knocked_down(agent_id: int) -> bool:
    aid = int(agent_id or 0)
    if aid <= 0:
        return False
    try:
        return bool(Agent.IsKnockedDown(aid))
    except Exception:
        return False


def _wisdom_energy_points(account) -> float:
    try:
        fraction = float(account.AgentData.Energy.Current)
        maximum = float(account.AgentData.Energy.Max)
        if fraction < 0.0 or maximum <= 0.0:
            return -1.0
        return max(0.0, fraction) * maximum
    except Exception:
        return -1.0


def _wisdom_profession(account: AccountStruct) -> int:
    try:
        aid = int(account.AgentData.AgentID or 0)
        if aid > 0 and Agent.IsValid(aid):
            primary, _secondary = Agent.GetProfessionIDs(aid)
            if int(primary) > 0:
                return int(primary)
    except Exception:
        pass
    try:
        return int(account.AgentData.Profession[0] or 0)
    except Exception:
        return 0


def _wisdom_carrier_role(account) -> str:
    ids = set(_wisdom_skill_ids(account))
    if int(Energy_Surge_ID) in ids:
        return "esurge"
    if int(Ineptitude_ID) in ids:
        return "ineptitude"
    if int(Soul_Twisting_ID) in ids:
        return "st"
    if int(Blood_is_Power_ID) in ids:
        return "bip"
    mesmer_id = int(getattr(Profession.Mesmer, "value", Profession.Mesmer))
    if _wisdom_profession(account) == mesmer_id:
        return "mesmer"
    return "other"


def _wisdom_same_instance_accounts() -> list[AccountStruct]:
    """Return live player accounts in our exact shared party/instance scope."""
    try:
        email, _lock_scope = _owner_context()
        if not email:
            return []
        own = GLOBAL_CACHE.ShMem.GetAccountDataFromEmail(email)
        if own is None:
            return []
        omap = own.AgentData.Map
        oparty = int(own.AgentPartyData.PartyID or 0)
        # _owner_context's lock scope can fall back to PartyID. It is NOT
        # an IsolationGroupID: comparing the two hid every group-0 teammate.
        if oparty <= 0:
            return []
        group_id = int(own.IsolationGroupID or 0)
        rows = []
        for acc in (
            GLOBAL_CACHE.ShMem.GetAllAccounts().GetAllActivePlayers(sort_results=True, include_isolated=False) or []
        ):
            try:
                if int(acc.IsolationGroupID or 0) != group_id:
                    continue
                amap = acc.AgentData.Map
                if (
                    int(amap.MapID or 0) != int(omap.MapID or 0)
                    or int(amap.Region or 0) != int(omap.Region or 0)
                    or int(amap.District or 0) != int(omap.District or 0)
                    or int(amap.Language or 0) != int(omap.Language or 0)
                ):
                    continue
                if oparty > 0 and int(acc.AgentPartyData.PartyID or 0) != oparty:
                    continue
                rows.append(acc)
            except Exception:
                continue

        # Shared active-player enumeration can lag immediately after zoning.  The
        # local account record is already available above, so always merge it as
        # a fallback rather than hiding a locally equipped Wisdom carrier.
        own_email = str(getattr(own, "AccountEmail", "") or "")
        own_agent = int(getattr(own.AgentData, "AgentID", 0) or 0)
        if not any(
            str(getattr(row, "AccountEmail", "") or "") == own_email
            or (own_agent > 0 and int(getattr(row.AgentData, "AgentID", 0) or 0) == own_agent)
            for row in rows
        ):
            rows.append(own)
        return rows
    except Exception:
        return []



def esurge_aegis_actual_carriers() -> list[dict]:
    """Discover live eSurge player accounts that actually carry Aegis.

    No fixed owner order is required.  The 30 s recharge naturally removes the
    last caster from the ready pool, while an exclusive pending lease lets the
    next ready carrier win atomically.  This also handles HSR without assuming a
    deterministic recharge cycle.
    """
    out: list[dict] = []
    for acc in _wisdom_same_instance_accounts():
        ids = set(_wisdom_skill_ids(acc))
        if int(Energy_Surge_ID) not in ids or int(Aegis_ID) not in ids:
            continue
        aid = int(getattr(acc.AgentData, "AgentID", 0) or 0)
        if aid <= 0 or not _wisdom_agent_alive(aid, acc):
            continue
        out.append({
            "email": str(getattr(acc, "AccountEmail", "") or ""),
            "agent_id": int(aid),
        })
    out.sort(key=lambda row: (str(row.get("email", "")), int(row.get("agent_id", 0))))
    return out


def esurge_aegis_party_status() -> dict[str, int | bool | float]:
    """Observed Aegis coverage on real party members.

    ``remaining_ms`` is the maximum live timer because Aegis is a party-wide
    earshot enchantment and small formation differences can make one edge member
    lose coverage first.  ``coverage_ratio`` is diagnostic only; it never starts
    a duplicate cast while a healthy central layer is still visible.
    """
    try:
        from Py4GWCoreLib import Party
    except Exception:
        Party = None  # type: ignore[assignment]

    member_ids: set[int] = set()
    if Party is not None:
        try:
            for player in Party.GetPlayers() or []:
                login = int(getattr(player, "login_number", 0) or 0)
                aid = int(Party.Players.GetAgentIDByLoginNumber(login) or 0)
                if aid > 0:
                    member_ids.add(aid)
        except Exception:
            pass
        for getter_name in ("GetHeroes", "GetHenchmen"):
            try:
                for member in getattr(Party, getter_name)() or []:
                    aid = int(getattr(member, "agent_id", 0) or 0)
                    if aid > 0:
                        member_ids.add(aid)
            except Exception:
                continue
    if not member_ids:
        try:
            aid = int(Player.GetAgentID() or 0)
            if aid > 0:
                member_ids.add(aid)
        except Exception:
            pass

    living = 0
    covered = 0
    remaining = 0
    for aid in member_ids:
        try:
            if not Agent.IsAlive(int(aid)):
                continue
        except Exception:
            continue
        living += 1
        rem = 0
        present = False
        try:
            rem = max(0, int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(int(aid), int(Aegis_ID)) or 0))
            present = rem > 0
        except Exception:
            rem = 0
        if not present:
            try:
                present = bool(Routines.Checks.Agents.HasEffect(int(aid), int(Aegis_ID)))
            except Exception:
                present = False
        if present:
            covered += 1
            remaining = max(int(remaining), int(rem))

    return {
        "present": bool(covered > 0),
        "remaining_ms": max(0, int(remaining)),
        "covered": max(0, int(covered)),
        "living": max(0, int(living)),
        "coverage_ratio": (float(covered) / float(living)) if living > 0 else 0.0,
    }


def esurge_aegis_due(*, present: bool, remaining_ms: int, activation_ms: int) -> bool:
    """True when the next carrier should begin casting Aegis.

    The old layer is intentionally allowed to run almost to expiry.  This avoids
    wasting scarce total coverage when three short-duration Aegis copies are used
    against a fixed 30 s recharge.  The caller supplies Fast-Casting-adjusted
    activation time; no 9/10/11-second duration is hardcoded here.
    """
    if not bool(present):
        return True
    # Some effect backends expose presence without a reliable timer.  In that
    # case fail safe: wait for observed expiry instead of spamming overlapping
    # Aegis casts.  The normal Reforged path exposes a live millisecond timer and
    # therefore still pre-casts before expiry.
    if int(remaining_ms or 0) <= 0:
        return False
    # Stage 2.40 starts candidacy early enough that the 240 ms election itself
    # does not create an Aegis gap at layer expiry.
    lead = (
        max(250, int(activation_ms or 0))
        + int(ESURGE_AEGIS_PRECAST_MARGIN_MS)
        + int(ESURGE_AEGIS_ELECTION_SETTLE_MS)
    )
    return max(0, int(remaining_ms or 0)) <= int(lead)


def _esurge_aegis_rows(lock_id: int, target_id: int) -> list[dict[str, int | str]]:
    return _active_skill_target_lock_events((int(lock_id),), target_id=int(target_id))


def post_esurge_aegis_candidate(duration_ms: int = ESURGE_AEGIS_CANDIDATE_MS) -> bool:
    """Publish this player's Aegis readiness for the short election window.

    This intentionally does *not* use ``_try_lock`` as a mutex. ``PostLock`` is
    append-only shared memory, so multiple candidates are expected and are
    resolved deterministically after a short settle window.
    """
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode,
            WhiteboardReentryPolicy,
        )
        email, gid = _owner_context()
        now = int(_now_ms())
        if not email or now <= 0:
            return False
        for row in _esurge_aegis_rows(_ESURGE_AEGIS_CANDIDATE_LOCK, _ESURGE_AEGIS_CANDIDATE_TARGET):
            if str(row.get("owner", "")) == str(email):
                return True
        slot = GLOBAL_CACHE.ShMem.PostLock(
            str(email), int(WhiteboardLockKind.SKILL_TARGET),
            int(_ESURGE_AEGIS_CANDIDATE_LOCK), int(_ESURGE_AEGIS_CANDIDATE_TARGET),
            int(now) + max(700, int(duration_ms or ESURGE_AEGIS_CANDIDATE_MS)),
            int(gid), int(WhiteboardLockMode.SHARED), 8,
            int(WhiteboardReentryPolicy.NON_REENTRANT),
            int(WhiteboardClaimStrength.HARD),
        )
        return int(slot) != -1
    except Exception:
        return False


def release_esurge_aegis_candidate(reason: str = "") -> int:
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        if not email:
            return 0
        cleared = int(GLOBAL_CACHE.ShMem.GetAllAccounts().ClearLockByOwnerKindTarget(
            email, int(WhiteboardLockKind.SKILL_TARGET), int(_ESURGE_AEGIS_CANDIDATE_TARGET), int(gid)
        ) or 0)
        if cleared:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_ESURGE_AEGIS_CANDIDATE_RELEASE", reason=str(reason or "unspecified"),
                    cleared=int(cleared), policy="stage2_40_cross_account_two_phase_election",
                )
            except Exception:
                pass
        return int(cleared)
    except Exception:
        return 0


def esurge_aegis_election_state() -> dict[str, int | str | bool]:
    """Resolve the same next Aegis owner on every player client."""
    try:
        email, _gid = _owner_context()
        now = int(_now_ms())
        pending = _esurge_aegis_rows(_ESURGE_AEGIS_PENDING_LOCK, _ESURGE_AEGIS_PENDING_TARGET)
        if pending:
            pending.sort(key=lambda row: (int(row.get("posted_at", 0) or 0), str(row.get("owner", ""))))
            owner = str(pending[0].get("owner", "") or "")
            return {"ready": False, "blocked_by_pending": True, "winner": owner,
                    "local_is_winner": bool(email and owner == email), "candidate_count": 0,
                    "settle_remaining_ms": 0}
        rows = _esurge_aegis_rows(_ESURGE_AEGIS_CANDIDATE_LOCK, _ESURGE_AEGIS_CANDIDATE_TARGET)
        if not rows or now <= 0:
            return {"ready": False, "blocked_by_pending": False, "winner": "",
                    "local_is_winner": False, "candidate_count": 0,
                    "settle_remaining_ms": int(ESURGE_AEGIS_ELECTION_SETTLE_MS)}
        by_owner: dict[str, dict[str, int | str]] = {}
        for row in rows:
            owner = str(row.get("owner", "") or "")
            if not owner:
                continue
            prev = by_owner.get(owner)
            if prev is None or int(row.get("posted_at", 0) or 0) < int(prev.get("posted_at", 0) or 0):
                by_owner[owner] = row
        if not by_owner:
            return {"ready": False, "blocked_by_pending": False, "winner": "",
                    "local_is_winner": False, "candidate_count": 0,
                    "settle_remaining_ms": int(ESURGE_AEGIS_ELECTION_SETTLE_MS)}
        first = min(int(row.get("posted_at", 0) or 0) for row in by_owner.values())
        settle_until = int(first) + int(ESURGE_AEGIS_ELECTION_SETTLE_MS)
        remaining = max(0, int(settle_until) - int(now))
        eligible = sorted(owner for owner, row in by_owner.items()
                          if int(row.get("posted_at", 0) or 0) <= int(settle_until))
        winner = str(eligible[0]) if eligible else ""
        ready = bool(winner and remaining <= 0)
        return {"ready": bool(ready), "blocked_by_pending": False, "winner": str(winner),
                "local_is_winner": bool(ready and email and winner == email),
                "candidate_count": int(len(eligible)), "settle_remaining_ms": int(remaining)}
    except Exception:
        return {"ready": False, "blocked_by_pending": False, "winner": "",
                "local_is_winner": False, "candidate_count": 0,
                "settle_remaining_ms": int(ESURGE_AEGIS_ELECTION_SETTLE_MS)}


def reserve_esurge_aegis_cast(duration_ms: int = ESURGE_AEGIS_PENDING_MS) -> bool:
    """Publish pending only for the deterministic winner; fail closed."""
    state = esurge_aegis_election_state()
    if not bool(state.get("ready", False)) or not bool(state.get("local_is_winner", False)):
        return False
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode,
            WhiteboardReentryPolicy,
        )
        email, gid = _owner_context()
        now = int(_now_ms())
        if not email or now <= 0:
            return False
        if _esurge_aegis_rows(_ESURGE_AEGIS_PENDING_LOCK, _ESURGE_AEGIS_PENDING_TARGET):
            return False
        slot = GLOBAL_CACHE.ShMem.PostLock(
            str(email), int(WhiteboardLockKind.SKILL_TARGET),
            int(_ESURGE_AEGIS_PENDING_LOCK), int(_ESURGE_AEGIS_PENDING_TARGET),
            int(now) + max(1200, int(duration_ms or ESURGE_AEGIS_PENDING_MS)),
            int(gid), int(WhiteboardLockMode.EXCLUSIVE), 1,
            int(WhiteboardReentryPolicy.NON_REENTRANT),
            int(WhiteboardClaimStrength.HARD),
        )
        if int(slot) == -1:
            return False
        pending = _esurge_aegis_rows(_ESURGE_AEGIS_PENDING_LOCK, _ESURGE_AEGIS_PENDING_TARGET)
        owners = sorted({str(row.get("owner", "") or "") for row in pending if str(row.get("owner", "") or "")})
        if owners and owners[0] != str(email):
            release_esurge_aegis_pending("post_verify_lost")
            return False
        return True
    except Exception:
        return False


def release_esurge_aegis_pending(reason: str = "") -> int:
    """Release only this account's synthetic eSurge-Aegis pending lease."""
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        if not email:
            return 0
        cleared = int(GLOBAL_CACHE.ShMem.GetAllAccounts().ClearLockByOwnerKindTarget(
            email, int(WhiteboardLockKind.SKILL_TARGET), int(_ESURGE_AEGIS_PENDING_TARGET), int(gid)
        ) or 0)
        if cleared:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_ESURGE_AEGIS_PENDING_RELEASE",
                    reason=str(reason or "unspecified"), cleared=int(cleared),
                    policy="stage2_40_cross_account_two_phase_election",
                )
            except Exception:
                pass
        return int(cleared)
    except Exception:
        return 0

def wisdom_actual_carriers() -> list[dict]:
    """Discover the *actual live skillbars* carrying Wisdom in this instance."""
    out: list[dict] = []
    for acc in _wisdom_same_instance_accounts():
        ids = set(_wisdom_skill_ids(acc))
        if int(Ebon_Battle_Standard_of_Wisdom_ID) not in ids:
            continue
        aid = int(getattr(acc.AgentData, "AgentID", 0) or 0)
        if aid <= 0:
            continue
        out.append({
            "email": str(getattr(acc, "AccountEmail", "") or ""),
            "agent_id": aid,
            "role": _wisdom_carrier_role(acc),
            "party_pos": int(getattr(acc.AgentPartyData, "PartyPosition", 99) or 99),
            "account": acc,
            "rank": _wisdom_rank_for_account(acc),
        })
    return out


def _wisdom_spell_activity(account) -> tuple[int, int, float]:
    """Return (spell slots, ready spell slots, minimum usable spell cost)."""
    total = 0
    ready = 0
    positive_costs: list[int] = []
    try:
        for row in account.AgentData.Skillbar.Skills:
            sid = int(getattr(row, "Id", 0) or 0)
            if sid <= 0 or sid == int(Ebon_Battle_Standard_of_Wisdom_ID):
                continue
            if not _wisdom_is_spell_skill(sid):
                continue
            total += 1
            if float(getattr(row, "Recharge", 0.0) or 0.0) <= 0.0:
                ready += 1
            cost = _wisdom_energy_cost(sid)
            if cost > 0:
                positive_costs.append(cost)
    except Exception:
        pass
    minimum = float(min(positive_costs)) if positive_costs else 0.0
    return int(total), int(ready), minimum


def _wisdom_followup_reserve(account) -> float:
    """Energy points to preserve for one important post-Wisdom action.

    This is role/skill aware rather than a fixed energy percentage.  It reserves
    the actual cost of the most expensive currently-ready high-value action on
    the equipped bar, falling back to the cheapest equipped high-value action
    when none is currently ready.
    """
    ids = set(_wisdom_skill_ids(account))
    role = _wisdom_carrier_role(account)
    if role == "esurge":
        important = (Energy_Surge_ID, Cry_of_Frustration_ID, Power_Spike_ID, Complicate_ID)
    elif role == "ineptitude":
        important = (Ineptitude_ID, Frustration_ID, Power_Drain_ID)
    else:
        important = tuple(sid for sid in ids if _wisdom_is_spell_skill(sid))

    equipped = [int(sid) for sid in important if int(sid) in ids and int(sid) != int(Ebon_Battle_Standard_of_Wisdom_ID)]
    if not equipped:
        return 0.0
    ready_costs: list[int] = []
    all_costs: list[int] = []
    for sid in equipped:
        cost = _wisdom_energy_cost(sid)
        if cost > 0:
            all_costs.append(cost)
        row = _wisdom_skill_row(account, sid)
        if row is not None and float(getattr(row, "Recharge", 0.0) or 0.0) <= 0.0 and cost > 0:
            ready_costs.append(cost)
    if ready_costs:
        return float(max(ready_costs))
    return float(min(all_costs)) if all_costs else 0.0


def _wisdom_beneficiaries() -> list[dict]:
    _WISDOM_BENEFICIARY_EXCLUSIONS.clear()
    mesmer_id = int(getattr(Profession.Mesmer, "value", Profession.Mesmer))
    rows: list[dict] = []
    accounts = _wisdom_same_instance_accounts()
    candidates = list(accounts)
    for owner in accounts:
        try:
            candidates.extend(GLOBAL_CACHE.ShMem.GetHeroesFromPlayers(int(owner.AgentData.AgentID)) or [])
        except Exception:
            pass
    seen: set[int] = set()
    for acc in candidates:
        aid = int(getattr(acc.AgentData, "AgentID", 0) or 0)
        if aid in seen:
            continue
        seen.add(aid)
        if aid <= 0 or not _wisdom_agent_alive(aid, acc) or _wisdom_agent_knocked_down(aid):
            _WISDOM_BENEFICIARY_EXCLUSIONS[aid] = "invalid_dead_or_knocked_down"
            continue
        ids = set(_wisdom_skill_ids(acc))
        is_mesmer = _wisdom_profession(acc) == mesmer_id
        is_bip = int(Blood_is_Power_ID) in ids
        is_st = int(Soul_Twisting_ID) in ids
        is_roj = int(Ray_of_Judgment_ID) in ids
        if not (is_mesmer or is_bip or is_st or is_roj):
            _WISDOM_BENEFICIARY_EXCLUSIONS[aid] = "not_spell_role"
            continue
        total_spells, ready_spells, min_cost = _wisdom_spell_activity(acc)
        # ST is not counted just because it exists: Binding Rituals are Rituals,
        # not spells. It only contributes if its actual equipped bar contains a
        # spell-family skill that can benefit from Wisdom.
        if is_st and not is_mesmer and total_spells <= 0:
            _WISDOM_BENEFICIARY_EXCLUSIONS[aid] = "no_spell_slots"
            continue
        energy = _wisdom_energy_points(acc)
        can_pay_one = bool(min_cost <= 0.0 or energy < 0.0 or energy + 0.01 >= min_cost)
        if not can_pay_one:
            _WISDOM_BENEFICIARY_EXCLUSIONS[aid] = "no_energy_for_spell"
            continue
        xy = _wisdom_xy(aid, acc)
        if xy is None:
            _WISDOM_BENEFICIARY_EXCLUSIONS[aid] = "position_unavailable"
            continue
        rows.append(
            {
                "agent_id": aid,
                "email": str(getattr(acc, "AccountEmail", "") or ""),
                "xy": xy,
                "is_mesmer": bool(is_mesmer),
                "is_bip": bool(is_bip),
                "is_st": bool(is_st),
                "is_roj": bool(is_roj),
                "spell_slots": int(total_spells),
                "ready_spell_slots": int(ready_spells),
                "moving": bool(_wisdom_agent_moving(aid)),
                "energy_points": float(energy),
            }
        )
    return rows


def _wisdom_coverage(center_xy: tuple[float, float], beneficiaries: list[dict]) -> dict:
    mesmers: list[int] = []
    stationary_mesmers: list[int] = []
    bip: list[int] = []
    st: list[int] = []
    roj: list[int] = []
    all_ids: list[int] = []
    spell_weight = 0.0
    for row in beneficiaries:
        try:
            dist = float(Utils.Distance(center_xy, row["xy"]))
        except Exception:
            continue
        if dist > float(Range.Area.value):
            continue
        aid = int(row["agent_id"])
        all_ids.append(aid)
        if row["is_mesmer"]:
            mesmers.append(aid)
            if not row["moving"] and dist <= float(WISDOM_STABLE_MARGIN):
                stationary_mesmers.append(aid)
            # Mesmers are the primary beneficiaries. Ready spell slots add a
            # small tie-breaker without allowing BiP/ST to outrank a Mesmer.
            spell_weight += 1.0 + min(0.30, 0.05 * int(row["ready_spell_slots"]))
        elif row["is_bip"]:
            bip.append(aid)
            spell_weight += 0.35 + min(0.15, 0.03 * int(row["ready_spell_slots"]))
        elif row["is_st"]:
            st.append(aid)
            spell_weight += min(0.25, 0.05 * int(row["ready_spell_slots"]))
        elif row.get("is_roj", False):
            roj.append(aid)
            spell_weight += 0.75 + min(0.25, 0.05 * int(row["ready_spell_slots"]))
    return {
        "mesmers": tuple(sorted(set(mesmers))),
        "stationary_mesmers": tuple(sorted(set(stationary_mesmers))),
        "bip": tuple(sorted(set(bip))),
        "st": tuple(sorted(set(st))),
        "roj": tuple(sorted(set(roj))),
        "all": tuple(sorted(set(all_ids))),
        "spell_weight": float(spell_weight),
    }


def _wisdom_fight_context(elapsed_ms: int) -> dict:
    """Conservative estimate of whether another useful spell round remains."""
    try:
        from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
        anchor = int(TeamCombatFocus.get_team_cluster_anchor(
            filter_range=Range.Spellcast.value,
            minimum_enemies=1,
            consumer_role="mesmerway_wisdom",
        ) or 0)
        if anchor <= 0:
            return {"worthwhile": False, "reason": "no_focus", "anchor": 0, "members": ()}
        members = [
            int(x) for x in TeamCombatFocus.get_team_cluster_members(
                anchor, radius=Range.Adjacent.value, filter_range=Range.Spellcast.value
            ) or []
            if _valid_enemy(int(x))
        ]
        if anchor not in members and _valid_enemy(anchor):
            members.append(anchor)
        members = sorted(set(members))
    except Exception:
        return {"worthwhile": False, "reason": "focus_error", "anchor": 0, "members": ()}

    hp_fraction_sum = 0.0
    current_hp_sum = 0.0
    max_hp_sum = 0.0
    boss = False
    for eid in members:
        try:
            hp = max(0.0, float(Agent.GetHealth(eid) or 0.0))
        except Exception:
            hp = 0.0
        try:
            mhp = max(0.0, float(Agent.GetMaxHealth(eid) or 0.0))
        except Exception:
            mhp = 0.0
        hp_fraction_sum += hp
        max_hp_sum += mhp
        current_hp_sum += hp * mhp if mhp > 0.0 else hp * 500.0
        try:
            boss = boss or bool(Agent.HasBossGlow(eid))
        except Exception:
            pass

    count = len(members)
    if count <= 0:
        worthwhile = False
        reason = "no_live_enemy"
    elif count >= 3:
        worthwhile = bool(current_hp_sum >= 700.0 or hp_fraction_sum >= 0.75)
        reason = "multi_packet" if worthwhile else "packet_nearly_dead"
    elif count == 2:
        worthwhile = bool(boss or current_hp_sum >= 800.0 or hp_fraction_sum >= 0.90)
        reason = "two_target_sustained" if worthwhile else "two_target_cleanup"
    else:
        # A single normal cleanup foe should not trigger a 10e/20s PvE ward.
        # A boss or unusually large remaining HP pool may still justify it.
        eid = int(members[0])
        try:
            hp = max(0.0, float(Agent.GetHealth(eid) or 0.0))
            mhp = max(0.0, float(Agent.GetMaxHealth(eid) or 0.0))
        except Exception:
            hp, mhp = 0.0, 0.0
        current_abs = hp * mhp if mhp > 0.0 else 0.0
        worthwhile = bool(boss or (mhp >= 900.0 and hp >= 0.35) or current_abs >= 900.0)
        reason = "single_durable" if worthwhile else "single_cleanup"

    return {
        "worthwhile": bool(worthwhile),
        "reason": str(reason),
        "anchor": int(anchor),
        "members": tuple(members),
        "enemy_count": int(count),
        "hp_fraction_sum": float(hp_fraction_sum),
        "current_hp_sum": float(current_hp_sum),
        "max_hp_sum": float(max_hp_sum),
        "boss": bool(boss),
        "elapsed_ms": int(elapsed_ms or 0),
    }


def wisdom_active_records() -> list[dict]:
    """Read live spatial Wisdom leases from the shared whiteboard."""
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        now = _now_ms()
        if not email or now <= 0:
            return []
        out: list[dict] = []
        for _slot, intent in GLOBAL_CACHE.ShMem.GetAllAccounts().GetAllIntents() or []:
            try:
                if int(getattr(intent, "ExpiresAtTick", 0) or 0) <= now:
                    continue
                if int(getattr(intent, "KindID", 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET):
                    continue
                if int(getattr(intent, "SkillID", 0) or 0) != int(_WISDOM_ACTIVE_LOCK):
                    continue
                if gid > 0 and int(getattr(intent, "IsolationGroupID", 0) or 0) != int(gid):
                    continue
                key = int(getattr(intent, "TargetAgentID", 0) or 0)
                if key <= 0:
                    continue
                out.append({
                    "owner": str(getattr(intent, "OwnerEmail", "") or ""),
                    "xy": _wisdom_unpack_xy(key),
                    "spatial_key": key,
                    "posted_at": int(getattr(intent, "PostedAtTick", 0) or 0),
                    "expires_at": int(getattr(intent, "ExpiresAtTick", 0) or 0),
                    "remaining_ms": max(0, int(getattr(intent, "ExpiresAtTick", 0) or 0) - int(now)),
                })
            except Exception:
                continue
        return out
    except Exception:
        return []


def _wisdom_pending_blocked() -> bool:
    return bool(_is_locked(_WISDOM_PENDING_LOCK, _WISDOM_PENDING_TARGET))


def reserve_wisdom_cast(caster_id: int = 0) -> bool:
    """Short group-wide cast lease; spatial active coverage is evaluated separately."""
    if _wisdom_pending_blocked():
        return False
    return bool(_try_lock(_WISDOM_PENDING_LOCK, _WISDOM_PENDING_TARGET, WISDOM_PENDING_MS))


def release_wisdom_pending(reason: str = "") -> int:
    """Release only this account's synthetic Wisdom-pending lease."""
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        if not email:
            return 0
        cleared = int(GLOBAL_CACHE.ShMem.GetAllAccounts().ClearLockByOwnerKindTarget(
            email, int(WhiteboardLockKind.SKILL_TARGET), int(_WISDOM_PENDING_TARGET), int(gid)
        ) or 0)
        if cleared:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event("MESMERWAY_WISDOM_PENDING_RELEASE", reason=str(reason or "unspecified"), cleared=int(cleared))
            except Exception:
                pass
        return cleared
    except Exception:
        return 0


def register_wisdom_cast(duration_ms: int | None = None, caster_id: int = 0, *,
                         cast_xy: tuple[float, float] | None = None, completed_tick: int = 0) -> dict:
    """Publish an estimated active ward at the caster's *cast position*.

    Guild Wars exposes the cast command and recharge but not a reliable Wisdom
    ground-effect object through this project API.  The active record therefore
    means "dispatch/activation window accepted and no local KD/death observed",
    not proof that an HSR proc occurred.  That distinction is logged explicitly.
    """
    cid = int(caster_id or Player.GetAgentID() or 0)
    try:
        xy = cast_xy if cast_xy is not None else Agent.GetXY(cid)
        xy = (float(xy[0]), float(xy[1]))
    except Exception:
        try:
            xy = Player.GetXY()
            xy = (float(xy[0]), float(xy[1]))
        except Exception:
            release_wisdom_pending("register_no_position")
            return {}
    rank = wisdom_local_rank()
    actual_duration = int(duration_ms if duration_ms is not None else wisdom_duration_ms(rank))
    actual_duration = max(5000, actual_duration)
    now = _now_ms()
    if completed_tick > 0:
        actual_duration = max(0, actual_duration - max(0, now - completed_tick))
    if actual_duration <= 0:
        release_wisdom_pending("already_expired")
        return {}
    spatial_key = _wisdom_pack_xy(*xy)
    release_wisdom_pending("cast_registered")
    posted = bool(_try_lock(_WISDOM_ACTIVE_LOCK, spatial_key, actual_duration))
    now = _now_ms()
    return {
        "posted": bool(posted),
        "xy": xy,
        "spatial_key": int(spatial_key),
        "rank": int(rank),
        "hsr_pct": int(wisdom_hsr_chance_pct(rank)),
        "duration_ms": int(actual_duration),
        "started_at": int(now),
        "expected_end": int(now + actual_duration) if now > 0 else 0,
    }


def _wisdom_candidate_rows(beneficiaries: list[dict]) -> list[dict]:
    carriers = wisdom_actual_carriers()
    # Stage 2.00 carries forward the audit fix: filter by real availability
    # before role preference.  A recharging/dead/moving Mesmer must not hide a
    # ready ST/HR fallback carrier.
    rows: list[dict] = []
    wisdom_cost = float(_wisdom_energy_cost(Ebon_Battle_Standard_of_Wisdom_ID) or 10)
    for carrier in carriers:
        acc = carrier["account"]
        aid = int(carrier["agent_id"])
        if not _wisdom_agent_alive(aid, acc) or _wisdom_agent_knocked_down(aid):
            continue
        row = _wisdom_skill_row(acc, Ebon_Battle_Standard_of_Wisdom_ID)
        if row is not None and float(getattr(row, "Recharge", 0.0) or 0.0) > 0.0:
            continue
        if int(getattr(acc.AgentData.Skillbar, "CastingSkillID", 0) or 0) not in (0, int(Ebon_Battle_Standard_of_Wisdom_ID)):
            continue
        xy = _wisdom_xy(aid, acc)
        if xy is None or _wisdom_agent_moving(aid):
            continue
        energy = _wisdom_energy_points(acc)
        reserve = _wisdom_followup_reserve(acc)
        if energy >= 0.0 and energy + 0.01 < wisdom_cost + reserve:
            continue
        coverage = _wisdom_coverage(xy, beneficiaries)
        rank = int(carrier["rank"])
        rows.append({
            **carrier,
            "xy": xy,
            "coverage": coverage,
            "energy_points": float(energy),
            "wisdom_cost": float(wisdom_cost),
            "followup_reserve": float(reserve),
            "energy_surplus": float(energy - wisdom_cost - reserve) if energy >= 0.0 else 0.0,
            "duration_ms": int(wisdom_duration_ms(rank)),
            "hsr_pct": int(wisdom_hsr_chance_pct(rank)),
        })
    return rows


def _wisdom_selected_carrier(beneficiaries: list[dict]) -> dict | None:
    rows = _wisdom_candidate_rows(beneficiaries)
    if not rows:
        return None
    # Coverage first; then stationary dwell and useful spell activity.  Mesmer
    # role is only a tie-breaker among candidates that are actually available.
    mesmer_roles = {"esurge", "ineptitude", "mesmer"}
    rows.sort(key=lambda r: (
        len(r["coverage"]["mesmers"]),
        len(r["coverage"]["stationary_mesmers"]),
        float(r["coverage"]["spell_weight"]),
        1 if r.get("role") in mesmer_roles else 0,
        int(r["duration_ms"]),
        float(r["energy_surplus"]),
        -int(r["party_pos"]),
        str(r["email"]),
    ), reverse=True)
    return rows[0]


def _wisdom_active_coverage(records: list[dict], beneficiaries: list[dict]) -> list[dict]:
    out = []
    for record in records:
        coverage = _wisdom_coverage(record["xy"], beneficiaries)
        out.append({**record, "coverage": coverage})
    return out


def _wisdom_log_loadout() -> None:
    global _WISDOM_LOADOUT_SIGNATURE, _wisdom_team_loadout_signature
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug

        accounts = _wisdom_same_instance_accounts()
        team_signature = ";".join(
            f"{int(acc.AgentData.AgentID)}:{','.join(map(str, _wisdom_skill_ids(acc)))}" for acc in accounts
        )
        if team_signature != _wisdom_team_loadout_signature:
            _wisdom_team_loadout_signature = team_signature
            for acc in accounts:
                CombatDebug.log_event(
                    "MESMERWAY_TEAM_SKILLBAR",
                    agent_id=int(acc.AgentData.AgentID),
                    party_position=int(acc.AgentPartyData.PartyPosition),
                    primary_profession=_wisdom_profession(acc),
                    ordered_skill_ids=",".join(map(str, _wisdom_skill_ids(acc))),
                    controller_version="stage1_30m_reforged_merge",
                )
        carriers = wisdom_actual_carriers()
        signature = ";".join(
            f"{c['party_pos']}:{c['role']}:{c['agent_id']}:{c['rank']}" for c in carriers
        )
        if signature == _WISDOM_LOADOUT_SIGNATURE:
            return
        _WISDOM_LOADOUT_SIGNATURE = signature
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_WISDOM_LOADOUT",
            carrier_count=int(len(carriers)),
            carriers=str(signature or "none"),
            policy="actual_shared_skillbars_esurge_ineptitude_preferred_legacy_carriers_fallback",
        )
    except Exception:
        pass


def wisdom_cast_plan(caster_id: int, elapsed_ms: int, carrier_role: str = "") -> dict:
    """Return a coordinated Wisdom decision for the local equipped carrier."""
    _wisdom_log_loadout()
    elapsed = max(0, int(elapsed_ms or 0))
    cid = int(caster_id or 0)
    base = {
        "allowed": False, "reason": "unknown", "caster_id": cid,
        "elapsed_ms": elapsed, "required_mesmers": WISDOM_DEFAULT_REQUIRED_MESMERS,
        "living_mesmers": 0, "mesmers_in_area": 0, "stationary_mesmers_in_area": 0,
        "active_wards": 0, "selected_agent_id": 0, "selected_role": "",
        "fight_reason": "", "fight_enemy_count": 0,
    }
    if cid <= 0:
        return {**base, "reason": "no_caster"}
    if elapsed < WISDOM_MIN_COMBAT_MS:
        return {**base, "reason": "settling_after_contact"}

    fight = _wisdom_fight_context(elapsed)
    base.update(
        fight_reason=str(fight.get("reason", "")),
        fight_enemy_count=int(fight.get("enemy_count", 0) or 0),
    )
    if not bool(fight.get("worthwhile", False)):
        return {**base, "reason": "fight_not_worthward"}

    beneficiaries = _wisdom_beneficiaries()
    eligible_mesmers = sum(1 for b in beneficiaries if b["is_mesmer"])
    living_mesmers = sum(
        1
        for acc in _wisdom_same_instance_accounts()
        if _wisdom_profession(acc) == int(Profession.Mesmer.value)
        and _wisdom_agent_alive(int(acc.AgentData.AgentID), acc)
    )
    required = (
        WISDOM_LONG_FIGHT_REQUIRED_MESMERS
        if elapsed >= WISDOM_RELAX_TO_TWO_MESMERS_MS
        else WISDOM_DEFAULT_REQUIRED_MESMERS
    )
    base.update(
        living_mesmers=int(living_mesmers),
        eligible_mesmers=int(eligible_mesmers),
        required_mesmers=int(required),
        beneficiary_exclusions=";".join(f"{aid}:{why}" for aid, why in sorted(_WISDOM_BENEFICIARY_EXCLUSIONS.items())),
        beneficiary_scan_complete=True,
    )
    selected = _wisdom_selected_carrier(beneficiaries)
    if selected is None:
        return {**base, "reason": "no_ready_energy_safe_carrier"}
    coverage = selected["coverage"]
    base.update(
        selected_agent_id=int(selected["agent_id"]),
        selected_role=str(selected["role"]),
        mesmers_in_area=int(len(coverage["mesmers"])),
        stationary_mesmers_in_area=int(len(coverage["stationary_mesmers"])),
        bip_in_area=int(len(coverage["bip"])),
        st_in_area=int(len(coverage["st"])),
        roj_in_area=int(len(coverage["roj"])),
        energy_points=round(float(selected["energy_points"]), 2),
        wisdom_cost=round(float(selected["wisdom_cost"]), 2),
        followup_reserve=round(float(selected["followup_reserve"]), 2),
        energy_surplus=round(float(selected["energy_surplus"]), 2),
        rank=int(selected["rank"]),
        hsr_pct=int(selected["hsr_pct"]),
        duration_ms=int(selected["duration_ms"]),
        center_x=round(float(selected["xy"][0]), 1),
        center_y=round(float(selected["xy"][1]), 1),
    )
    if int(selected["agent_id"]) != cid:
        return {**base, "reason": "better_carrier_selected"}
    if len(coverage["mesmers"]) < int(required):
        return {**base, "reason": "insufficient_mesmer_coverage"}
    # Early plants need a stable core. In a longer fight two stationary Mesmers
    # are enough to justify a refresh/reposition while others kite briefly.
    stationary_required = 2 if int(required) >= 2 else 1
    if len(coverage["stationary_mesmers"]) < stationary_required:
        return {**base, "reason": "team_still_moving"}

    active = _wisdom_active_coverage(wisdom_active_records(), beneficiaries)
    base["active_wards"] = int(len(active))
    candidate_xy = selected["xy"]
    candidate_mesmers = set(coverage["mesmers"])
    covered_by_active: set[int] = set()
    nearest_distance = 999999.0
    nearest_remaining = 0
    best_active_mesmers = 0
    for record in active:
        active_mesmers = set(record["coverage"]["mesmers"])
        covered_by_active.update(active_mesmers)
        best_active_mesmers = max(best_active_mesmers, len(active_mesmers))
        try:
            dist = float(Utils.Distance(candidate_xy, record["xy"]))
        except Exception:
            dist = 999999.0
        if dist < nearest_distance:
            nearest_distance = dist
            nearest_remaining = int(record["remaining_ms"])

    base.update(
        active_mesmers_current=int(best_active_mesmers),
        nearest_active_distance=round(float(nearest_distance), 1) if active else -1.0,
        nearest_active_remaining_ms=int(nearest_remaining),
        newly_covered_mesmers=int(len(candidate_mesmers - covered_by_active)),
    )

    if active:
        new_mesmers = candidate_mesmers - covered_by_active
        # Refresh only close to expiry. This deliberately avoids assuming that
        # overlapping copies of the same Wisdom ward stack their HSR chances.
        if nearest_distance <= WISDOM_RELOCATE_DISTANCE and nearest_remaining > WISDOM_RENEW_LEAD_MS:
            return {**base, "reason": "active_ward_still_useful"}
        if nearest_distance <= WISDOM_RELOCATE_DISTANCE and nearest_remaining <= WISDOM_RENEW_LEAD_MS:
            return {**base, "allowed": True, "reason": "renew_before_expiry"}
        # Team moved away: old spatial lease must not block a useful new plant.
        if best_active_mesmers <= 1 and len(candidate_mesmers) >= int(required):
            return {**base, "allowed": True, "reason": "relocate_after_team_move"}
        # Split formation: require at least two newly covered Mesmers so tiny
        # position changes do not create ward spam.
        if len(new_mesmers) >= WISDOM_SECOND_GROUP_MIN_NEW_MESMERS:
            return {**base, "allowed": True, "reason": "second_spatial_group"}
        return {**base, "reason": "active_ward_no_incremental_coverage"}

    if _wisdom_pending_blocked():
        return {**base, "reason": "another_carrier_pending"}
    return {**base, "allowed": True, "reason": "early_primary_plant"}


def log_wisdom_plan(plan: dict, *, role: str = "") -> None:
    """Bounded decision telemetry: reason changes immediately, stable holds every 4 s."""
    try:
        cid = int(plan.get("caster_id", 0) or 0)
        if cid <= 0:
            return
        now = _now_ms()
        reason = str(plan.get("reason", "unknown") or "unknown")
        previous_reason, previous_tick = _WISDOM_PLAN_LOG_STATE.get(cid, ("", 0))
        if reason == previous_reason and now > 0 and now - int(previous_tick) < 4000:
            return
        _WISDOM_PLAN_LOG_STATE[cid] = (reason, int(now))
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_WISDOM_DECISION",
            allowed=bool(plan.get("allowed", False)),
            reason=reason,
            role=str(role or plan.get("selected_role", "") or ""),
            selected_agent_id=int(plan.get("selected_agent_id", 0) or 0),
            living_mesmers=int(plan.get("living_mesmers", 0) or 0),
            eligible_mesmers=int(plan.get("eligible_mesmers", 0) or 0),
            beneficiary_scan_complete=bool(plan.get("beneficiary_scan_complete", False)),
            beneficiary_exclusions=str(plan.get("beneficiary_exclusions", "not_scanned")),
            roj_in_area=int(plan.get("roj_in_area", 0) or 0),
            mesmers_in_area=int(plan.get("mesmers_in_area", 0) or 0),
            stationary_mesmers_in_area=int(plan.get("stationary_mesmers_in_area", 0) or 0),
            required_mesmers=int(plan.get("required_mesmers", 0) or 0),
            active_wards=int(plan.get("active_wards", 0) or 0),
            nearest_active_remaining_ms=int(plan.get("nearest_active_remaining_ms", 0) or 0),
            newly_covered_mesmers=int(plan.get("newly_covered_mesmers", 0) or 0),
            fight_reason=str(plan.get("fight_reason", "") or ""),
            fight_enemy_count=int(plan.get("fight_enemy_count", 0) or 0),
            energy_points=float(plan.get("energy_points", -1.0) or -1.0),
            followup_reserve=float(plan.get("followup_reserve", 0.0) or 0.0),
        )
    except Exception:
        pass


def confirm_local_wisdom_activation(dispatch_tick: int = 0, receipt: dict | None = None):
    """Observe the local 1 s Wisdom activation before publishing an active ward.

    Fast Casting does not shorten this 1 s non-Mesmer PvE skill.  The API does
    not expose a Wisdom ground-effect object, so this confirms only that the
    local Wisdom cast survived approximately its normal activation window.  An
    early disappearance/KD/death is treated as failed and the pending lease is
    released so another carrier may retry.
    """
    if False:
        yield
    cid = int(Player.GetAgentID() or 0)
    start = int(dispatch_tick or _now_ms() or 0)
    try:
        activation_ms = max(1, int(round(float(Skill.Data.GetActivation(int(Ebon_Battle_Standard_of_Wisdom_ID)) or 1.0) * 1000.0)))
    except Exception:
        activation_ms = 1000
    # Non-Mesmer 1 s PvE skill: Fast Casting does not reduce this activation.
    success_not_before = int(start + max(650, activation_ms - 170)) if start > 0 else 0
    deadline = int(start + activation_ms + 450) if start > 0 else 0
    seen_cast = False
    first_seen = 0
    last_seen = 0
    native_started = False
    queued_at = start

    while True:
        now = _now_ms()
        if receipt is not None:
            actual_dispatch = int(receipt.get("tick", 0) or 0)
            if not actual_dispatch:
                if now - queued_at > 3500:
                    release_wisdom_pending("dispatch_timeout")
                    return False, "dispatch_timeout"
                yield from Routines.Yield.wait(40)
                continue
            start = actual_dispatch
            success_not_before = start + max(650, activation_ms - 170)
            deadline = start + activation_ms + 450
        try:
            from Py4GWCoreLib.Builds.Skills.ReforgedSupport import peek_local_cast_events, match_local_cast_outcome
            events = peek_local_cast_events()
            native_started, outcome = match_local_cast_outcome(
                events, cid, int(Ebon_Battle_Standard_of_Wisdom_ID), 0, start, native_started)
            if outcome in ("stopped", "interrupted", "superseded"):
                release_wisdom_pending("native_" + outcome)
                return False, "native_" + outcome
            if outcome == "finished":
                if receipt is not None:
                    receipt["finished_ms"] = next((int(e[0]) for e in events
                        if e[0] >= start and e[1] == 4 and e[2] == cid
                        and e[3] in (0, int(Ebon_Battle_Standard_of_Wisdom_ID))), now)
                return True, "native_finished"
        except Exception:
            pass
        if seen_cast and now - last_seen > 250:
            release_wisdom_pending("observation_gap")
            return False, "observation_gap"
        try:
            alive = bool(Agent.IsAlive(cid))
        except Exception:
            alive = True
        try:
            knocked = bool(Agent.IsKnockedDown(cid))
        except Exception:
            knocked = False
        if not alive or knocked:
            release_wisdom_pending("activation_dead_or_kd")
            return False, "dead_or_knocked_down"
        try:
            current = int(Agent.GetCastingSkillID(cid) or 0) if Agent.IsCasting(cid) else 0
        except Exception:
            current = 0
        if current == int(Ebon_Battle_Standard_of_Wisdom_ID):
            if not seen_cast:
                first_seen = int(now)
            seen_cast = True
            last_seen = int(now)
        elif seen_cast:
            # Cast stopped. Completion at/near the natural 1 s activation window
            # is accepted; a materially earlier stop is an interruption/cancel.
            if success_not_before <= 0 or now >= success_not_before:
                if receipt is not None:
                    receipt["finished_ms"] = now
                return True, "activation_window_observed"
            release_wisdom_pending("activation_ended_early")
            return False, "ended_early"

        if deadline > 0 and now >= deadline:
            # Still casting is not a completed ward.
            # Some client snapshots can miss a short casting-state transition.
            # Do not publish a 20 s spatial ward lease from a totally unobserved
            # activation; allow another carrier to retry after the pending lease.
            release_wisdom_pending("activation_not_observed")
            return False, "not_observed"
        yield from Routines.Yield.wait(40)

def wisdom_mesmer_readiness(caster_id: int, elapsed_ms: int) -> tuple[bool, int, int, int]:
    """Backward-compatible wrapper used by legacy optional HR/ST carriers.

    Stage 1.28 now performs actual-carrier selection, energy/dwell/fight checks,
    and spatial active-ward evaluation under the hood.
    """
    plan = wisdom_cast_plan(int(caster_id or 0), int(elapsed_ms or 0), carrier_role="legacy")
    return (
        bool(plan.get("allowed", False)),
        int(plan.get("living_mesmers", 0) or 0),
        int(plan.get("mesmers_in_area", 0) or 0),
        int(plan.get("required_mesmers", WISDOM_DEFAULT_REQUIRED_MESMERS) or WISDOM_DEFAULT_REQUIRED_MESMERS),
    )


def _wisdom_observer_email() -> str:
    """One deterministic client logs estimated covered spell starts."""
    try:
        rows = []
        for acc in _wisdom_same_instance_accounts():
            ids = set(_wisdom_skill_ids(acc))
            if int(Energy_Surge_ID) not in ids and int(Ineptitude_ID) not in ids:
                continue
            rows.append((int(getattr(acc.AgentPartyData, "PartyPosition", 99) or 99), str(getattr(acc, "AccountEmail", "") or "")))
        rows.sort()
        return rows[0][1] if rows else ""
    except Exception:
        return ""


def observe_wisdom_activity() -> None:
    """Low-noise observation of spell starts while position/time says Wisdom covers them.

    This measures observed shared casting state inside an estimated live ward. It
    does NOT claim that the random HSR proc occurred; the game does not expose
    that proc directly through this project API.
    """
    try:
        me, _gid = _owner_context()
        if not me or me != _wisdom_observer_email():
            return
        records = wisdom_active_records()
        accounts = _wisdom_same_instance_accounts()
        if not records:
            for acc in accounts:
                aid = int(getattr(acc.AgentData, "AgentID", 0) or 0)
                if aid > 0:
                    _WISDOM_OBS_LAST_CAST[aid] = int(getattr(acc.AgentData.Skillbar, "CastingSkillID", 0) or 0)
            return
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        for acc in accounts:
            aid = int(getattr(acc.AgentData, "AgentID", 0) or 0)
            if aid <= 0 or not _wisdom_agent_alive(aid, acc):
                continue
            sid = int(getattr(acc.AgentData.Skillbar, "CastingSkillID", 0) or 0)
            prev = int(_WISDOM_OBS_LAST_CAST.get(aid, 0) or 0)
            _WISDOM_OBS_LAST_CAST[aid] = sid
            if sid <= 0 or sid == prev or not _wisdom_is_spell_skill(sid):
                continue
            xy = _wisdom_xy(aid, acc)
            if xy is None:
                continue
            covered = []
            for record in records:
                try:
                    if float(Utils.Distance(xy, record["xy"])) <= float(Range.Area.value):
                        covered.append(record)
                except Exception:
                    continue
            if not covered:
                continue
            best = max(covered, key=lambda r: int(r.get("remaining_ms", 0) or 0))
            CombatDebug.log_event(
                "MESMERWAY_WISDOM_COVERED_SPELL_START",
                agent_id=int(aid), skill_id=int(sid),
                ward_owner=str(best.get("owner", "")),
                ward_remaining_ms=int(best.get("remaining_ms", 0) or 0),
                observation="shared_cast_start_plus_position_time_estimate_not_hsr_proc_confirmation",
            )
    except Exception:
        pass

def try_claim_panic_packet(anchor_id: int, duration_ms: int = 2200) -> bool:
    """One shared Panic owner per authoritative packet for a short dispatch window.

    Player-account Panic and Hero Panic both use this lease.  It prevents two
    Panic copies from being committed into the same packet on the same frame
    while remaining short enough not to suppress the next legitimate refresh.
    """
    aid = int(anchor_id or 0)
    if aid <= 0:
        return False
    return bool(_try_lock(_PANIC_PACKET_LOCK, aid, max(700, int(duration_ms))))

def _profession_value(p) -> int:
    try:
        return int(getattr(p, "value", p) or 0)
    except Exception:
        return 0


def _profession_name_from_rc4(agent_id: int) -> str:
    try:
        from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
        from Py4GWCoreLib.Builds.Knowledge import OfflineKnowledge
        rec = EnemyKnowledge.observe(int(agent_id), 0)
        profile = OfflineKnowledge.enemy_profile(rec.model_id, rec.name, rec.map_id)
        return str(profile.get("primary_profession", "") or "").casefold()
    except Exception:
        return ""


def estimate_enemy_max_energy(agent_id: int) -> float:
    """Conservative first-contact Energy prior from live/RC4 profession data."""
    aid = int(agent_id or 0)
    primary = 0
    try:
        p, _s = Agent.GetProfessions(aid)
        primary = _profession_value(p)
    except Exception:
        try:
            p, _s = Agent.GetProfessionIDs(aid)
            primary = _profession_value(p)
        except Exception:
            primary = 0

    names = {
        _profession_value(Profession.Warrior): "warrior",
        _profession_value(Profession.Ranger): "ranger",
        _profession_value(Profession.Monk): "monk",
        _profession_value(Profession.Necromancer): "necromancer",
        _profession_value(Profession.Mesmer): "mesmer",
        _profession_value(Profession.Elementalist): "elementalist",
        _profession_value(Profession.Assassin): "assassin",
        _profession_value(Profession.Ritualist): "ritualist",
        _profession_value(Profession.Paragon): "paragon",
        _profession_value(Profession.Dervish): "dervish",
    }
    pname = names.get(primary, "") or _profession_name_from_rc4(aid)
    if pname == "warrior":
        return 20.0
    if pname in ("ranger", "assassin", "dervish"):
        return 25.0
    if pname == "elementalist":
        return 50.0
    if pname in ("monk", "necromancer", "mesmer", "ritualist", "paragon"):
        return 30.0

    try:
        from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
        scores = EnemyKnowledge.role_scores(aid)
        if int(scores.get("caster", 0)) >= 60:
            return 30.0
    except Exception:
        pass
    return 25.0


def _locked_count(lock_ids: tuple[int, ...], target_id: int) -> int:
    return sum(1 for lock_id in lock_ids if _is_locked(lock_id, int(target_id)))


def _live_lock_count(lock_ids: tuple[int, ...], target_id: int) -> int:
    """Count whiteboard locks without the frame-cached IsLockBlocked path.

    Stage 1.25 used ``_is_locked`` to count eSurge reservations. That helper is
    intentionally frame-cached inside SharedMemory; a lock posted earlier in the
    same frame can therefore still read as the previous cached ``False`` value.
    The 2026-09-21 live log exposed this as ``pending_esurges=0`` on every cast.

    eSurge projection needs the freshly posted lock immediately, so read the
    underlying whiteboard CountLocks implementation directly. This is scoped to
    reservation accounting only; all other whiteboard users keep their existing
    cached behaviour.
    """
    aid = int(target_id or 0)
    if aid <= 0:
        return 0
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardReentryPolicy,
        )
        _email, gid = _owner_context()
        now = _now_ms()
        if now <= 0:
            return int(_locked_count(lock_ids, aid))
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccounts()
        count = 0
        for lock_id in lock_ids:
            count += int(all_accounts.CountLocks(
                int(WhiteboardLockKind.SKILL_TARGET), int(lock_id), int(aid),
                int(gid), "", int(now),
                int(WhiteboardReentryPolicy.NON_REENTRANT),
                int(WhiteboardClaimStrength.HARD),
            ) or 0)
        return max(0, int(count))
    except Exception:
        return int(_locked_count(lock_ids, aid))


def _active_skill_target_lock_events(
    lock_ids: tuple[int, ...] | None = None,
    *,
    target_id: int | None = None,
    key_min: int | None = None,
    key_max: int | None = None,
) -> list[dict[str, int | str]]:
    """Read fresh whiteboard lock records, including PostedAtTick.

    Energy reset ordering cannot use the frame-cached boolean lock helpers: an
    Aneurysm refill invalidates *older* drains but not eSurges or neighbour drains
    posted after that refill.  PostedAtTick gives us that ordering without adding
    another persistence/shared-memory abstraction.
    """
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        _email, gid = _owner_context()
        now = int(_now_ms())
        if now <= 0:
            return []
        wanted = {int(v) for v in (lock_ids or ())}
        out: list[dict[str, int | str]] = []
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccounts()
        for intent in all_accounts.Intents:
            if not getattr(intent, "Active", False):
                continue
            if int(getattr(intent, "ExpiresAtTick", 0) or 0) <= now:
                continue
            if int(getattr(intent, "KindID", 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET):
                continue
            if int(getattr(intent, "IsolationGroupID", 0) or 0) != int(gid):
                continue
            key = int(getattr(intent, "SkillID", 0) or 0)
            if wanted and key not in wanted:
                continue
            if key_min is not None and key < int(key_min):
                continue
            if key_max is not None and key > int(key_max):
                continue
            tid = int(getattr(intent, "TargetAgentID", 0) or 0)
            if target_id is not None and tid != int(target_id):
                continue
            purpose_by_key = {
                int(_SHATTER_DELUSIONS_TARGET_LOCK): "shatter_target_reservation",
                int(_HEX_MUTATION_LOCK): "hex_mutation_dispatch_guard",
                int(_FRAGILITY_TARGET_LOCK): "fragility_center_reservation",
                int(_FRAGILITY_FEEDER_PENDING_LOCK): "fragility_pending_feeder",
                int(_FRAGILITY_FEEDER_ACTIVE_LOCK): "fragility_active_feeder",
                int(_PHANTOM_TARGET_LOCK): "phantom_setup_reservation",
                int(_WEB_TARGET_LOCK): "web_setup_reservation",
                int(_MIND_WRACK_CAST_LOCK): "mind_wrack_reservation",
            }
            out.append({
                "key": int(key),
                "target_id": int(tid),
                "posted_at": int(getattr(intent, "PostedAtTick", 0) or 0),
                "expires_at": int(getattr(intent, "ExpiresAtTick", 0) or 0),
                "owner": str(getattr(intent, "OwnerEmail", "") or ""),
                "purpose": str(purpose_by_key.get(int(key), f"lock_{int(key)}")),
            })
        return out
    except Exception:
        return []



def _current_identity_guard(agent_id: int) -> tuple[str, int, int]:
    """Return (session_key, agent_generation, rows_valid_since_tick)."""
    aid = int(agent_id or 0)
    now = int(_now_ms() or 0)
    if aid <= 0 or now <= 0:
        return "", 0, 0
    session_key = ""
    session_floor = 0
    try:
        from Py4GWCoreLib.Builds.Skills import CombatSession
        snap = CombatSession.refresh()
        session_key = str(getattr(snap, "key", "") or "")
        uptime = max(0, int(getattr(snap, "instance_uptime_ms", 0) or 0))
        session_floor = max(0, now - uptime - 1000)
    except Exception:
        session_key = ""
    generation = 1
    try:
        from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
        generation = max(1, int(EnemyKnowledge.agent_generation(aid) or 1))
    except Exception:
        generation = 1
    previous = _IDENTITY_ROW_GUARD.get(aid)
    if previous is None:
        since = int(session_floor)
    elif str(previous[0]) != session_key or int(previous[1]) != int(generation):
        # A known session/generation transition is an identity boundary. Existing
        # rows from the previous occupant/session are never inherited.
        since = int(now)
    else:
        since = max(int(session_floor), int(previous[2]))
    _IDENTITY_ROW_GUARD[aid] = (str(session_key), int(generation), int(since))
    return str(session_key), int(generation), int(since)


def _row_matches_current_identity(agent_id: int, posted_at: int) -> bool:
    aid, posted = int(agent_id or 0), int(posted_at or 0)
    if aid <= 0 or posted <= 0:
        return False
    _session, _generation, since = _current_identity_guard(aid)
    return posted >= int(since or 0)


def _stable_application_token(skill_id: int, agent_id: int, row: dict[str, int | str]) -> int:
    """Deterministic cross-client token for one concrete shared feeder row."""
    values = [int(skill_id or 0), int(agent_id or 0), int(row.get("posted_at", 0) or 0), int(row.get("expires_at", 0) or 0)]
    owner = str(row.get("owner", "") or "")
    h = 2166136261
    for value in values:
        value &= 0xFFFFFFFF
        for shift in (0, 8, 16, 24):
            h ^= (value >> shift) & 0xFF
            h = (h * 16777619) & 0xFFFFFFFF
    for ch in owner.encode("utf-8", errors="ignore"):
        h ^= int(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return max(1, int(h & 0x7FFFFFFF))


def _optional_application_retired(token: int) -> bool:
    return bool(int(token or 0) > 0 and _is_locked(_OPTIONAL_FEEDER_APPLICATION_RETIRED_LOCK, int(token)))


def _retire_optional_application(token: int, *, target_id: int, skill_id: int, reason: str, removal_observed: bool) -> bool:
    token = int(token or 0)
    if token <= 0:
        return False
    newly_retired = bool(_try_lock(_OPTIONAL_FEEDER_APPLICATION_RETIRED_LOCK, token, OPTIONAL_FEEDER_APPLICATION_RETIRED_MS))
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_OPTIONAL_APPLICATION_RETIRED", target_id=int(target_id or 0),
            feeder_skill_id=int(skill_id or 0), application_token=token,
            newly_retired=bool(newly_retired), removal_observed=bool(removal_observed),
            reason=str(reason or ""),
            policy="stage1_66_exact_application_retirement_not_removal_claim",
        )
    except Exception:
        pass
    return bool(newly_retired)


def _hex_evidence_invalidation_key(skill_id: int, agent_id: int) -> int:
    sid, aid = int(skill_id or 0), int(agent_id or 0)
    ordered = tuple(int(v) for v in _HEX_LIFETIME_LOCK_BY_SKILL.keys() if int(v or 0) > 0)
    try:
        skill_index = ordered.index(sid)
    except ValueError:
        return 0
    return int(_HEX_EVIDENCE_INVALIDATED_BASE + int(skill_index) * 0x10000 + (aid & 0xFFFF))


def _latest_hex_invalidation_cutoff(agent_id: int, skill_id: int) -> int:
    key = int(_hex_evidence_invalidation_key(skill_id, agent_id) or 0)
    now = int(_now_ms() or 0)
    if key <= 0 or now <= 0:
        return 0
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        _email, gid = _owner_context()
        values = []
        for intent in GLOBAL_CACHE.ShMem.GetAllAccounts().Intents:
            if not getattr(intent, "Active", False):
                continue
            if int(getattr(intent, "ExpiresAtTick", 0) or 0) <= now:
                continue
            if int(getattr(intent, "KindID", 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET):
                continue
            if int(getattr(intent, "IsolationGroupID", 0) or 0) != int(gid):
                continue
            if int(getattr(intent, "SkillID", 0) or 0) != key:
                continue
            values.append(int(getattr(intent, "TargetAgentID", 0) or 0))
        return max(values, default=0)
    except Exception:
        return 0


def _invalidate_hex_lifetime_evidence(
    agent_id: int, skill_id: int, lease_posted_at: int, *, reason: str, removal_observed: bool = False,
) -> bool:
    """Invalidate only lifetime evidence at/before one concrete application.

    This does not assert that the game effect was observed removed. It says only
    that the old timing lease is no longer safe presence evidence after the
    resolved mutation. A later application has a newer PostedAtTick and survives.
    """
    aid, sid, cutoff = int(agent_id or 0), int(skill_id or 0), int(lease_posted_at or 0)
    key = int(_hex_evidence_invalidation_key(sid, aid) or 0)
    if aid <= 0 or sid <= 0 or cutoff <= 0 or key <= 0:
        return False
    ttl = max(int(HEX_EVIDENCE_INVALIDATED_MIN_MS), int(_expected_hex_duration_ms(sid) or 0) + 1500)
    posted = bool(_try_lock(key, cutoff, ttl))
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_HEX_LIFETIME_INVALIDATED", target_id=aid, skill_id=sid,
            lease_posted_at=cutoff, removal_observed=bool(removal_observed),
            reason=str(reason or ""), posted=bool(posted),
            policy="stage1_66_application_scoped_evidence_invalidation",
        )
    except Exception:
        pass
    return posted


def _valid_hex_lifetime_rows(agent_id: int, skill_id: int) -> list[dict[str, int | str]]:
    aid, sid = int(agent_id or 0), int(skill_id or 0)
    lock_id = int(_HEX_LIFETIME_LOCK_BY_SKILL.get(sid, 0) or 0)
    if aid <= 0 or lock_id <= 0:
        return []
    cutoff = int(_latest_hex_invalidation_cutoff(aid, sid) or 0)
    rows = _active_skill_target_lock_events((lock_id,), target_id=aid)
    return [row for row in rows if int(row.get("posted_at", 0) or 0) > cutoff and _row_matches_current_identity(aid, int(row.get("posted_at", 0) or 0))]


def _latest_hex_lifetime_lease_posted_at(agent_id: int, skill_id: int) -> int:
    rows = _valid_hex_lifetime_rows(int(agent_id), int(skill_id))
    return max((int(row.get("posted_at", 0) or 0) for row in rows), default=0)

def _latest_aneurysm_refill_tick(agent_id: int) -> int:
    rows = _active_skill_target_lock_events((_ANEURYSM_REFILLED_LOCK,), target_id=int(agent_id))
    return max((int(row["posted_at"]) for row in rows), default=0)


def _count_lock_events_since(lock_ids: tuple[int, ...], agent_id: int, since_tick: int = 0) -> int:
    rows = _active_skill_target_lock_events(tuple(int(v) for v in lock_ids), target_id=int(agent_id))
    return sum(1 for row in rows if int(row["posted_at"]) >= int(since_tick or 0))


def _aneurysm_neighbor_drain_estimate(agent_id: int, since_tick: int = 0) -> float:
    rows = _active_skill_target_lock_events(
        target_id=int(agent_id),
        key_min=int(_ANEURYSM_DRAIN_KEY_BASE) + 1,
        key_max=int(_ANEURYSM_DRAIN_KEY_BASE) + int(_ANEURYSM_DRAIN_KEY_MAX),
    )
    total = 0.0
    for row in rows:
        if int(row["posted_at"]) < int(since_tick or 0):
            continue
        total += float(max(0, int(row["key"]) - int(_ANEURYSM_DRAIN_KEY_BASE)))
    return max(0.0, float(total))


def is_aneurysm_drained_neighbor(agent_id: int) -> bool:
    aid = int(agent_id or 0)
    if aid <= 0:
        return False
    refill_tick = int(_latest_aneurysm_refill_tick(aid))
    return _aneurysm_neighbor_drain_estimate(aid, refill_tick) > 0.0


def famine_local_duration_ms() -> int:
    """Expected Famine lifespan from the local carrier's Wilderness Survival."""
    rank = max(0, int(_current_skill_attribute_level(Famine_ID) or 0))
    return max(30000, int(30 + 14 * rank) * 1000)


def famine_active_state() -> dict[str, object]:
    now = int(_now_ms() or 0)
    rows = _active_skill_target_lock_events((_FAMINE_ACTIVE_LOCK,))
    if now <= 0 or not rows:
        return {"active": False, "remaining_ms": 0, "xy": None, "covered": False, "owner": ""}
    row = max(rows, key=lambda item: int(item.get("posted_at", 0) or 0))
    xy = _winter_unpack_spatial_key(int(row.get("target_id", 0) or 0))
    remaining = max(0, int(row.get("expires_at", 0) or 0) - now)
    covered = False
    try:
        covered = bool(Utils.Distance(Player.GetXY(), xy) <= float(FAMINE_COVERAGE_RADIUS))
    except Exception:
        pass
    return {
        "active": bool(remaining > 0), "remaining_ms": int(remaining), "xy": xy,
        "covered": bool(covered), "owner": str(row.get("owner", "") or ""),
        "posted_at": int(row.get("posted_at", 0) or 0),
    }


def famine_should_cast() -> bool:
    if int(Famine_ID or 0) <= 0:
        return False
    if _is_locked(_FAMINE_PENDING_LOCK, _FAMINE_PENDING_TARGET):
        return False
    state = famine_active_state()
    if not bool(state.get("active", False)) or not bool(state.get("covered", False)):
        return True
    return int(state.get("remaining_ms", 0) or 0) <= int(FAMINE_REFRESH_LEAD_MS)


def reserve_famine_cast() -> bool:
    if _is_locked(_FAMINE_PENDING_LOCK, _FAMINE_PENDING_TARGET):
        return False
    return bool(_try_lock(_FAMINE_PENDING_LOCK, _FAMINE_PENDING_TARGET, FAMINE_PENDING_MS))


def release_famine_pending(reason: str = "") -> int:
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        if not email:
            return 0
        cleared = int(GLOBAL_CACHE.ShMem.GetAllAccounts().ClearLockByOwnerKindTarget(
            email, int(WhiteboardLockKind.SKILL_TARGET), int(_FAMINE_PENDING_TARGET), int(gid)
        ) or 0)
        if cleared:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event("MESMERWAY_FAMINE_PENDING_RELEASE", reason=str(reason or "unspecified"), cleared=int(cleared))
            except Exception:
                pass
        return cleared
    except Exception:
        return 0


def register_famine_cast(*, cast_xy: tuple[float, float] | None = None) -> dict[str, object]:
    now = int(_now_ms() or 0)
    try:
        xy = tuple(cast_xy) if cast_xy is not None else tuple(Player.GetXY())
        x, y = float(xy[0]), float(xy[1])
    except Exception:
        x = y = 0.0
    duration = int(famine_local_duration_ms())
    target = int(_winter_pack_spatial_key(x, y, now))
    release_famine_pending("cast_registered")
    posted = bool(_try_lock(_FAMINE_ACTIVE_LOCK, target, duration))
    record = {"active": bool(posted), "remaining_ms": int(duration), "xy": (x, y), "covered": bool(posted)}
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_FAMINE_CAST", x=round(x, 1), y=round(y, 1), duration_ms=int(duration),
            posted=bool(posted), policy="stage2_11_shared_famine_nature_spirit_lease",
        )
    except Exception:
        pass
    return record


def active_aneurysm_anchor_ids() -> list[int]:
    """Newest refilled anchors first; dead/expired targets are omitted."""
    rows = _active_skill_target_lock_events((_ANEURYSM_REFILLED_LOCK,))
    rows.sort(key=lambda row: int(row["posted_at"]), reverse=True)
    result: list[int] = []
    for row in rows:
        aid = int(row["target_id"])
        if aid <= 0 or aid in result or not _valid_enemy(aid):
            continue
        result.append(aid)
    return result


def active_famine_esurge_focus_ids() -> list[int]:
    """Newest Stage 2.15 Famine/eSurge setup batteries first."""
    rows = _active_skill_target_lock_events((_FAMINE_ESURGE_FOCUS_LOCK,))
    rows.sort(key=lambda row: int(row["posted_at"]), reverse=True)
    result: list[int] = []
    for row in rows:
        aid = int(row.get("target_id", 0) or 0)
        if aid <= 0 or aid in result or not _valid_enemy(aid):
            continue
        result.append(aid)
    return result


def current_domination_level() -> int:
    """Return the local Mesmer's live effective Domination rank.

    AttributeStruct.level is intentionally used instead of level_base so live
    modifiers such as Heroic Refrain and consumables are included.
    """
    try:
        attrs = Agent.GetAttributes(int(Player.GetAgentID() or 0)) or []
        for attr in attrs:
            try:
                name = str(attr.GetName() or "").replace("_", " ").strip().casefold()
            except Exception:
                name = ""
            if name == "domination magic":
                return max(0, int(getattr(attr, "level", 0) or getattr(attr, "level_base", 0) or 0))
    except Exception:
        pass
    return 0


def current_esurge_drain() -> float:
    """Resolve Energy Surge's current Energy loss from live Domination rank."""
    rank = int(current_domination_level())
    try:
        for attr_name, field_name, values in Skill.GetProgressionData(Energy_Surge_ID):
            if str(attr_name).strip().casefold() != "domination magic":
                continue
            if str(field_name).replace(" ", "").strip().casefold() not in ("energyloss", "energydrain"):
                continue
            table = {int(k): float(v) for k, v in dict(values or {}).items()}
            if not table:
                continue
            if rank in table:
                return max(1.0, float(table[rank]))
            # Fail safely for unexpected ranks: use the closest known rank.
            nearest = min(table, key=lambda key: abs(int(key) - int(rank)))
            return max(1.0, float(table[nearest]))
    except Exception:
        pass
    return float(ESURGE_ESTIMATED_DRAIN_FALLBACK)


def recent_esurge_drain_estimate(agent_id: int) -> float:
    """Committed eSurge drain *after* the latest Aneurysm refill, if any."""
    aid = int(agent_id or 0)
    if aid <= 0:
        return 0.0
    reset_tick = int(_latest_aneurysm_refill_tick(aid))
    hit_count = int(_count_lock_events_since(_ESURGE_HIT_LOCKS, aid, reset_tick))
    return min(
        estimate_enemy_max_energy(aid),
        float(hit_count) * float(current_esurge_drain()),
    )


def estimate_enemy_current_energy(agent_id: int) -> float:
    """Short-horizon committed Energy estimate with ordered Aneurysm resets.

    A refill invalidates older eSurge/adjacent-drain evidence, but drains posted
    after the refill still count.  This fixes the old 4.2 s behaviour where a
    refilled target incorrectly looked permanently full even after fresh eSurges.
    """
    aid = int(agent_id or 0)
    max_energy = float(estimate_enemy_max_energy(aid))
    if aid <= 0:
        return max_energy
    reset_tick = int(_latest_aneurysm_refill_tick(aid))
    esurge_loss = float(recent_esurge_drain_estimate(aid))
    neighbour_loss = float(_aneurysm_neighbor_drain_estimate(aid, reset_tick))
    mind_wrack_loss = float(_count_lock_events_since(_MIND_WRACK_DRAIN_LOCKS, aid, reset_tick))
    return max(0.0, max_energy - esurge_loss - neighbour_loss - mind_wrack_loss)


def esurge_reservation_state(agent_id: int) -> dict[str, float | int]:
    """Return landed and pending eSurge pressure for one target.

    ``_ESURGE_HIT_LOCKS`` are short evidence for accepted/committed cast dispatches;
    ``_ESURGE_SLOT_LOCKS`` are target reservations that may still be in flight.
    During their overlap, ``max(slot_count-hit_count, 0)`` avoids double-counting
    the same cast.  This is deliberately a projection, not measured enemy Energy.
    """
    aid = int(agent_id or 0)
    reset_tick = int(_latest_aneurysm_refill_tick(aid)) if aid > 0 else 0
    # Aneurysm is an Energy reset.  Old hit/slot leases must not poison the new
    # battery cycle; only reservations posted after the latest refill participate.
    hit_count = int(_count_lock_events_since(_ESURGE_HIT_LOCKS, aid, reset_tick)) if aid > 0 else 0
    slot_count = int(_count_lock_events_since(_ESURGE_SLOT_LOCKS, aid, reset_tick)) if aid > 0 else 0
    pending_count = max(0, int(slot_count) - int(hit_count))
    drain = float(current_esurge_drain())
    landed_energy = float(estimate_enemy_current_energy(aid))
    projected_energy = max(0.0, float(landed_energy) - float(pending_count) * float(drain))
    return {
        "hit_count": int(hit_count),
        "slot_count": int(slot_count),
        "pending_count": int(pending_count),
        "committed_estimated_energy": float(landed_energy),
        "projected_energy": float(projected_energy),
    }


def estimate_enemy_projected_energy(agent_id: int) -> float:
    """Estimated Energy after already-reserved eSurges, not an exact value."""
    return float(esurge_reservation_state(int(agent_id)).get("projected_energy", 0.0) or 0.0)


def _effect_remaining_ms(agent_id: int, skill_id: int) -> int:
    aid = int(agent_id or 0)
    sid = int(skill_id or 0)
    if aid <= 0 or sid <= 0:
        return 0
    try:
        return max(0, int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(aid, sid) or 0))
    except Exception:
        return 0


def _current_skill_attribute_level(skill_id: int) -> int:
    """Live effective rank for the local caster, including HR/cons/runes."""
    sid = int(skill_id or 0)
    if sid <= 0:
        return 0
    try:
        attr = Skill.Attribute.GetAttribute(sid)
        attr_id = int(getattr(attr, "value", attr) or 0)
        for row in Agent.GetAttributes(int(Player.GetAgentID() or 0)) or []:
            if int(getattr(row, "attribute_id", 0) or 0) != attr_id:
                continue
            return max(0, int(getattr(row, "level", 0) or getattr(row, "level_base", 0) or 0))
    except Exception:
        pass
    return 0


def _expected_hex_duration_ms(skill_id: int) -> int:
    """Expected active Hex duration for one native-finished local cast.

    Fixed-duration Hexes are explicit.  Attribute-scaled durations use the local
    skill progression table at the live effective rank.  This is timing evidence,
    not effect proof; Agent.IsHexed is still required when a lease substitutes for
    missing per-skill effect visibility.
    """
    sid = int(skill_id or 0)
    fixed = {
        int(Mistrust_ID or 0): 6000,
        int(Overload_ID or 0): 5000,
        int(Ineptitude_ID or 0): 4000,
        int(Wandering_Eye_ID or 0): 4000,
        int(Clumsiness_ID or 0): 4000,
        int(Phantom_Pain_ID or 0): 10000,
        int(Web_of_Disruption_ID or 0): 10000,
    }
    if sid in fixed:
        return int(fixed[sid])
    rank = int(_current_skill_attribute_level(sid))
    try:
        for _attr_name, field_name, values in Skill.GetProgressionData(sid):
            field = str(field_name).replace(" ", "").replace("_", "").casefold()
            if field != "duration":
                continue
            table = {int(k): float(v) for k, v in dict(values or {}).items()}
            if not table:
                continue
            use_rank = rank if rank in table else min(table, key=lambda k: abs(int(k) - int(rank)))
            return max(500, int(round(float(table[use_rank]) * 1000.0)))
    except Exception:
        pass
    return 0


def _publish_hex_lifetime_lease(receipt: CastReceipt) -> None:
    """Publish cast-owner timing after native completion/effect confirmation.

    The timing belongs only to the enemy that actually received the Hex.  The
    wider ``mutation_targets`` tuple is merely a dispatch/shatter exclusion
    packet (not proof that Fragility/Ineptitude etc. hexed every member).
    """
    sid = int(receipt.skill_id or 0)
    aid = int(receipt.target_id or 0)
    lock_id = int(_HEX_LIFETIME_LOCK_BY_SKILL.get(sid, 0) or 0)
    duration_ms = int(_expected_hex_duration_ms(sid) or 0)
    skip_reason = ""
    if aid <= 0 or not _valid_enemy(aid):
        skip_reason = "target_invalid_at_completion"
    elif lock_id <= 0:
        skip_reason = "skill_not_lifetime_tracked"
    elif duration_ms <= 0:
        skip_reason = "duration_unknown"
    if skip_reason:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_HEX_LIFETIME_LEASE_SKIPPED", skill_id=sid, target_id=aid,
                reason=skip_reason, duration_ms=int(duration_ms),
                evidence=str(receipt.evidence or ""),
                policy="stage1_61_post_mutex_single_target_hex_timing",
            )
        except Exception:
            pass
        return
    # Native outcome observation can trail actual FINISHED slightly. Shorten the
    # lease rather than pretending the effect lasts longer than the game skill.
    ttl = max(350, int(duration_ms) - 300)
    posted = bool(_try_lock(lock_id, aid, ttl))
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        if posted:
            CombatDebug.log_event(
                "MESMERWAY_HEX_LIFETIME_LEASE", skill_id=sid, target_id=aid,
                targets=str(aid), duration_ms=int(duration_ms), lease_ms=int(ttl),
                attribute_level=int(_current_skill_attribute_level(sid)),
                evidence=str(receipt.evidence or ("native_finished" if receipt.native_finished else "effect_observed")),
                policy="stage1_61_post_mutex_single_target_hex_timing",
            )
        else:
            CombatDebug.log_event(
                "MESMERWAY_HEX_LIFETIME_LEASE_SKIPPED", skill_id=sid, target_id=aid,
                reason="whiteboard_post_unavailable", duration_ms=int(duration_ms),
                evidence=str(receipt.evidence or ""),
                policy="stage1_61_post_mutex_single_target_hex_timing",
            )
    except Exception:
        pass


def _hex_lifetime_state(agent_id: int, skill_id: int) -> dict[str, object]:
    """Return observed/expected/unknown lifetime evidence without conflating them."""
    aid, sid = int(agent_id or 0), int(skill_id or 0)
    if aid <= 0 or sid <= 0 or not _valid_enemy(aid):
        return {"state": "absent", "remaining_ms": 0, "lease_posted_at": 0}
    direct = int(_effect_remaining_ms(aid, sid) or 0)
    if direct > 0:
        return {"state": "observed", "remaining_ms": int(direct), "lease_posted_at": 0}
    rows = _valid_hex_lifetime_rows(aid, sid)
    if not rows:
        return {"state": "unknown", "remaining_ms": 0, "lease_posted_at": 0}
    now = int(_now_ms() or 0)
    row = max(rows, key=lambda r: int(r.get("posted_at", 0) or 0))
    remaining = max(0, int(row.get("expires_at", 0) or 0) - now)
    if remaining <= 0:
        return {"state": "unknown", "remaining_ms": 0, "lease_posted_at": int(row.get("posted_at", 0) or 0)}
    try:
        generic_hexed = bool(Agent.IsHexed(aid))
    except Exception:
        generic_hexed = True  # observation unavailable, not proof of absence
    if not generic_hexed:
        # Cross-client observation can lag a just-finished remote cast. Keep a
        # deliberately short confirmation grace for a fresh native-finished
        # lease, then downgrade it to unknown. This preserves the proven team
        # bridge without letting historical lifetime estimates masquerade as
        # indefinite presence evidence.
        posted_at = int(row.get("posted_at", 0) or 0)
        age_ms = max(0, int(now - posted_at)) if posted_at > 0 else 1 << 30
        if age_ms <= int(HEX_LIFETIME_REMOTE_CONFIRM_GRACE_MS):
            return {
                "state": "expected", "remaining_ms": int(remaining),
                "lease_posted_at": int(posted_at), "evidence": "fresh_remote_grace",
            }
        # Once the generic game state proves the target is no longer hexed, an
        # older application-specific timing model must not revive later merely
        # because some different hex gets applied. The grace above protects a
        # just-finished remote cast from propagation lag; only older evidence is
        # tombstoned here.
        _invalidate_hex_lifetime_evidence(
            aid, sid, posted_at, reason="generic_unhexed_after_remote_grace",
            removal_observed=True,
        )
        return {
            "state": "unknown", "remaining_ms": 0,
            "lease_posted_at": int(posted_at), "evidence": "generic_unhexed_observed",
        }
    return {
        "state": "expected", "remaining_ms": int(remaining),
        "lease_posted_at": int(row.get("posted_at", 0) or 0),
    }


def _hex_lifetime_remaining_ms(agent_id: int, skill_id: int) -> int:
    return int(_hex_lifetime_state(int(agent_id), int(skill_id)).get("remaining_ms", 0) or 0)


def _lease_known_mesmer_hex_ids(agent_id: int) -> list[int]:
    aid = int(agent_id or 0)
    if aid <= 0:
        return []
    result = []
    for sid in _HEX_LIFETIME_LOCK_BY_SKILL:
        state = _hex_lifetime_state(aid, int(sid))
        if str(state.get("state", "")) in {"observed", "expected"} and int(state.get("remaining_ms", 0) or 0) > 0:
            result.append(int(sid))
    return sorted(set(result))


def _shatter_known_hex_ids(agent_id: int) -> list[int]:
    """Visible Mesmer hexes, or fresh timing-backed team evidence when hidden."""
    aid = int(agent_id or 0)
    if aid <= 0 or not _valid_enemy(aid):
        return []
    visible = [int(v) for v in _mesmer_hex_ids(aid) if int(v or 0) > 0]
    if visible:
        return visible
    try:
        if not Agent.IsHexed(aid):
            return []
    except Exception:
        return []
    # With detail hidden, preserve all still-valid expected leases. Multiple
    # candidates remain multiple and therefore force Shatter to fail closed.
    return _lease_known_mesmer_hex_ids(aid)


def _lock_remaining_ms(lock_id: int, target_id: int) -> int:
    now = int(_now_ms() or 0)
    if now <= 0:
        return 0
    rows = _active_skill_target_lock_events((int(lock_id),), target_id=int(target_id))
    return max((max(0, int(row.get("expires_at", 0) or 0) - now) for row in rows), default=0)


# Stage 1.45 condition observation for Epidemic value.  The effect cache is the
# preferred source because it exposes remaining duration; four conditions also
# have direct Agent flags and are used only as presence fallbacks when the effect
# cache is incomplete.  Unknown duration is represented as 0 and is not treated
# as a full-value spread by the planner.
_CONDITION_IDS: tuple[tuple[str, int], ...] = tuple(
    (name, int(sid or 0)) for name, sid in (
        ("bleeding", Bleeding_ID),
        ("burning", Burning_ID),
        ("poison", Poison_ID),
        ("disease", Disease_ID),
        ("blind", Blind_ID),
        ("dazed", Dazed_ID),
        ("crippled", Crippled_ID),
        ("deep_wound", Deep_Wound_ID),
        ("weakness", Weakness_ID),
        ("cracked_armor", Cracked_Armor_ID),
    ) if int(sid or 0) > 0
)
_CONDITION_BIT_BY_ID = {int(sid): (1 << idx) for idx, (_name, sid) in enumerate(_CONDITION_IDS)}
_CONDITION_NAME_BY_ID = {int(sid): str(name) for name, sid in _CONDITION_IDS}
_CONDITION_BASE_VALUE = {
    int(Deep_Wound_ID or 0): 6.0,
    int(Dazed_ID or 0): 5.0,
    int(Blind_ID or 0): 4.0,
    int(Weakness_ID or 0): 3.0,
    int(Burning_ID or 0): 2.0,
    int(Poison_ID or 0): 2.0,
    int(Bleeding_ID or 0): 1.5,
    int(Disease_ID or 0): 1.5,
    int(Crippled_ID or 0): 1.5,
    int(Cracked_Armor_ID or 0): 1.0,
}


def _condition_presence_fallback(agent_id: int, skill_id: int) -> bool:
    aid, sid = int(agent_id or 0), int(skill_id or 0)
    if aid <= 0 or sid <= 0:
        return False
    try:
        if sid == int(Deep_Wound_ID or 0):
            return bool(Agent.IsDeepWounded(aid))
        if sid == int(Bleeding_ID or 0):
            return bool(Agent.IsBleeding(aid))
        if sid == int(Crippled_ID or 0):
            return bool(Agent.IsCrippled(aid))
        if sid == int(Poison_ID or 0):
            return bool(Agent.IsPoisoned(aid))
    except Exception:
        pass
    return False


def observed_condition_state(agent_id: int) -> dict[int, int]:
    """Confirmed condition presence -> conservative remaining milliseconds.

    A positive native/effect observation is real evidence of presence even when
    the ally-effect cache cannot expose duration.  Stage 1.57 keeps that case
    usable for Epidemic with a short conservative horizon instead of silently
    converting unknown duration to zero.
    """
    aid = int(agent_id or 0)
    if aid <= 0:
        return {}
    state: dict[int, int] = {}
    for _name, sid in _CONDITION_IDS:
        effect_present = False
        direct_present = False
        try:
            effect_present = bool(Routines.Checks.Agents.HasEffect(aid, int(sid)))
        except Exception:
            effect_present = False
        if not effect_present:
            direct_present = bool(_condition_presence_fallback(aid, int(sid)))
        if not effect_present and not direct_present:
            continue
        remaining = int(_effect_remaining_ms(aid, int(sid)) or 0)
        if remaining <= 0:
            remaining = int(EPIDEMIC_CONFIRMED_PRESENT_UNKNOWN_DURATION_MS)
        state[int(sid)] = int(remaining)
    return state


def epidemic_source_condition_state(agent_id: int) -> tuple[dict[int, int], str]:
    """Condition state usable by Epidemic with explicit provenance.

    Normal entries remain observation-backed.  A natively finished, unambiguous
    Phantom Pain -> Shatter conversion is deterministic game provenance for Deep
    Wound even when this account's effect table misses the condition.  That case
    is short-lived, conservative, and labelled expected rather than observed.
    """
    aid = int(agent_id or 0)
    state = dict(observed_condition_state(aid))
    provenance = "observed" if state else "none"
    remote_observed = bool(_is_locked(_PHANTOM_DEEP_WOUND_SOURCE_LOCK, aid))
    expected = bool(_is_locked(_PHANTOM_DEEP_WOUND_EXPECTED_LOCK, aid))
    if aid > 0 and int(Deep_Wound_ID or 0) > 0 and int(Deep_Wound_ID or 0) not in state:
        if remote_observed:
            remaining = max(int(EPIDEMIC_MIN_CONDITION_REMAINING_MS), _lock_remaining_ms(_PHANTOM_DEEP_WOUND_SOURCE_LOCK, aid))
            state[int(Deep_Wound_ID)] = int(remaining)
            provenance = "phantom_end_observed_remote"
        elif expected:
            remaining = max(int(EPIDEMIC_MIN_CONDITION_REMAINING_MS), _lock_remaining_ms(_PHANTOM_DEEP_WOUND_EXPECTED_LOCK, aid))
            state[int(Deep_Wound_ID)] = int(remaining)
            provenance = "phantom_end_expected_unobserved"
    return state, provenance


def condition_observation_status(agent_id: int) -> dict[str, object]:
    """Separate confirmed absence from incomplete condition detail visibility."""
    aid = int(agent_id or 0)
    state = observed_condition_state(aid)
    if state:
        return {"state": state, "status": "specific_conditions_observed", "conditioned": True}
    try:
        conditioned = bool(Agent.IsConditioned(aid))
    except Exception:
        return {"state": {}, "status": "condition_state_unknown", "conditioned": None}
    if conditioned:
        return {"state": {}, "status": "conditioned_but_specifics_unobserved", "conditioned": True}
    return {"state": {}, "status": "native_no_condition", "conditioned": False}


def _condition_signature(state: dict[int, int]) -> int:
    signature = 0
    for sid in state:
        signature |= int(_CONDITION_BIT_BY_ID.get(int(sid), 0) or 0)
    return int(signature)


def _epidemic_recipient_signature_lock(signature: int) -> int:
    # Ten condition bits fit comfortably below 1024.  A changed source condition
    # set therefore receives a different short-lived served key and can be valued
    # again without allowing duplicate casts on the unchanged packet.
    return int(_EPIDEMIC_RECIPIENT_SIGNATURE_BASE + (int(signature) & 0x3FF))


def _adjacent_enemy_ids(center_id: int, *, include_center: bool = False) -> tuple[int, ...]:
    center = int(center_id or 0)
    if center <= 0:
        return ()
    try:
        center_xy = Agent.GetXY(center)
        enemies = AgentArray.GetEnemyArray() or []
    except Exception:
        return ()
    result: list[int] = []
    for raw in enemies:
        aid = int(raw or 0)
        if aid <= 0 or (aid == center and not include_center) or not _valid_enemy(aid):
            continue
        try:
            if Utils.Distance(center_xy, Agent.GetXY(aid)) <= float(Range.Adjacent.value):
                result.append(aid)
        except Exception:
            continue
    return tuple(sorted(set(result)))


def _enemy_role_scores(agent_id: int) -> dict[str, int]:
    try:
        from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
        return dict(EnemyKnowledge.role_scores(int(agent_id)) or {})
    except Exception:
        return {}


def _enemy_is_martial(agent_id: int) -> bool:
    aid = int(agent_id or 0)
    try:
        if Agent.IsAttacking(aid):
            return True
    except Exception:
        pass
    try:
        weapon_type = int(Agent.GetWeaponType(aid)[0] or 0)
        return weapon_type in {1, 2, 3, 4, 5, 6, 7}
    except Exception:
        return False


def current_shatter_delusions_profile() -> tuple[float, float]:
    """Return (primary, adjacent) PvE Shatter damage at local Domination rank."""
    rank = int(current_domination_level())
    primary = 75.0
    try:
        for attr_name, field_name, values in Skill.GetProgressionData(Shatter_Delusions_ID):
            if str(attr_name).strip().casefold() != "domination magic":
                continue
            if "damage" not in str(field_name).casefold():
                continue
            table = {int(k): float(v) for k, v in dict(values or {}).items()}
            if table:
                primary = float(table.get(rank, table[min(table, key=lambda key: abs(int(key) - rank))]))
                break
    except Exception:
        pass
    return float(primary), float(primary * 0.75)


def expected_shatter_packet_damage(agent_id: int) -> tuple[float, int]:
    primary, adjacent = current_shatter_delusions_profile()
    neighbors = len(_adjacent_enemy_ids(int(agent_id), include_center=False))
    return float(primary + adjacent * neighbors), int(1 + neighbors)

def is_mind_wrack_active(agent_id: int) -> bool:
    """Actual Mind Wrack when visible, plus only a short post-dispatch bridge."""
    aid = int(agent_id or 0)
    if aid <= 0 or int(Mind_Wrack_ID or 0) <= 0:
        return False
    try:
        if Routines.Checks.Agents.HasEffect(aid, int(Mind_Wrack_ID)):
            return True
    except Exception:
        pass
    return bool(_is_locked(_MIND_WRACK_ACTIVE_FALLBACK_LOCK, aid))


def current_mind_wrack_profile() -> tuple[float, float]:
    """Return (damage per owner-triggered Energy loss, zero-Energy burst).

    Values are derived from the bundled progression data when available. The
    conservative fallbacks are the documented standard PvE maxima, so a data
    naming mismatch cannot make eSurge overvalue the combo.
    """
    rank = int(current_domination_level())
    damage_per_energy = 25.0
    zero_burst = 100.0
    try:
        for attr_name, field_name, values in Skill.GetProgressionData(Mind_Wrack_ID):
            if str(attr_name).strip().casefold() != "domination magic":
                continue
            table = {int(k): float(v) for k, v in dict(values or {}).items()}
            if not table:
                continue
            value = table.get(rank)
            if value is None:
                nearest = min(table, key=lambda key: abs(int(key) - int(rank)))
                value = table[nearest]
            normalized = str(field_name).replace(" ", "").replace("_", "").strip().casefold()
            if "damageper" in normalized and "energy" in normalized:
                damage_per_energy = max(0.0, float(value))
            elif normalized == "damage" or (("zero" in normalized or "0" in normalized) and "damage" in normalized):
                # Bundled Mind Wrack progression names the zero-Energy burst
                # simply "Damage" (Dom19/20/21 => 123/128/134). Stage 1.35
                # missed that field and fell back to 100, undervaluing zeroing.
                zero_burst = max(0.0, float(value))
    except Exception:
        pass
    return float(damage_per_energy), float(zero_burst)


def register_mind_wrack_nonhex_trigger(target_id: int) -> bool:
    """Publish one owner-specific Mind Wrack 1-Energy trigger.

    The caller is responsible for proving ownership (the local controller keeps
    the targets on which *it* successfully cast Mind Wrack) and for proving the
    dispatched skill is a non-hex Mesmer skill.
    """
    aid = int(target_id or 0)
    if aid <= 0 or not is_mind_wrack_active(aid):
        return False
    for lock_id in _MIND_WRACK_DRAIN_LOCKS:
        if _is_locked(int(lock_id), aid):
            continue
        if _try_lock(int(lock_id), aid, MIND_WRACK_DRAIN_EVIDENCE_MS):
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_MIND_WRACK_TRIGGER",
                    target_id=int(aid),
                    projected_energy=f"{estimate_enemy_projected_energy(aid):.1f}",
                    policy="owner_nonhex_mesmer_targeting_minus1_energy",
                )
            except Exception:
                pass
            return True
    return False


def _mind_wrack_zero_bonus(agent_id: int, available_energy: float, effective_drain: float) -> float:
    """Expected single-target zero-Energy burst added only when this cast zeros it."""
    aid = int(agent_id or 0)
    energy = max(0.0, float(available_energy))
    drain = max(0.0, float(effective_drain))
    if aid <= 0 or energy <= 0.0 or not is_mind_wrack_active(aid):
        return 0.0
    if drain + 0.01 < energy:
        return 0.0
    _per_energy, zero_burst = current_mind_wrack_profile()
    return max(0.0, float(zero_burst))


def _ready_skill_holders_count(skill_id: int) -> int:
    ready = 0
    sid = int(skill_id or 0)
    if sid <= 0:
        return 0
    try:
        for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
            skillbar = getattr(getattr(account, "AgentData", None), "Skillbar", None)
            if skillbar is None or int(getattr(skillbar, "CastingSkillID", 0) or 0) > 0:
                continue
            for skill in getattr(skillbar, "Skills", ()) or ():
                if int(getattr(skill, "Id", 0) or 0) != sid:
                    continue
                if float(getattr(skill, "Recharge", 0.0) or 0.0) <= 0.0:
                    ready += 1
                break
    except Exception:
        pass
    return max(0, int(ready))


def _fragility_packet_ids(center_target_id: int) -> tuple[int, ...]:
    """Physical recipients, independent of canonical packet re-anchoring."""
    target = int(center_target_id or 0)
    if target <= 0:
        return ()
    members = set(int(aid) for aid in AgentArray.GetEnemyArray())
    members.add(target)
    try:
        center = Agent.GetXY(target)
    except Exception:
        return ()
    result: list[int] = []
    for enemy_id in members:
        eid = int(enemy_id or 0)
        if eid <= 0 or not _valid_enemy(eid):
            continue
        try:
            if Utils.Distance(center, Agent.GetXY(eid)) <= float(Range.Adjacent.value):
                result.append(eid)
        except Exception:
            continue
    return tuple(result)


def _fragility_generation_slot(now_ms: int | None = None) -> int:
    now = int(_now_ms() if now_ms is None else now_ms)
    if now <= 0:
        return 0
    return int((now // int(_FRAGILITY_GENERATION_BUCKET_MS)) % int(_FRAGILITY_GENERATION_SLOTS))


def _fragility_generation_active_lock(slot: int) -> int:
    return int(_FRAGILITY_GENERATION_ACTIVE_BASE) + (int(slot) % int(_FRAGILITY_GENERATION_SLOTS))


def _fragility_generation_consumed_lock(slot: int) -> int:
    return int(_FRAGILITY_GENERATION_CONSUMED_BASE) + (int(slot) % int(_FRAGILITY_GENERATION_SLOTS))


def _fragility_generation_for_target(agent_id: int) -> int:
    """Return current Fragility generation with at most two lock probes.

    A live 4.5 s feeder lease can only belong to the current 5 s bucket or the
    immediately previous bucket.  This keeps the hot path O(1).
    """
    aid = int(agent_id or 0)
    if aid <= 0:
        return -1
    current = int(_fragility_generation_slot())
    previous = int((current - 1) % int(_FRAGILITY_GENERATION_SLOTS))
    if _is_locked(_fragility_generation_active_lock(current), aid):
        return current
    if _is_locked(_fragility_generation_active_lock(previous), aid):
        return previous
    return -1


def _fragility_generation_consumed(agent_id: int, generation: int) -> bool:
    """Fresh cross-client consumption check without a generation scan.

    Stage 1.40 closed same-client reuse locally, but one live log still showed a
    cross-client duplicate.  The generation lock itself was correct; reading it
    through the frame-cached IsLockBlocked path could lag another account's post.
    Count exactly this one generation lock through the uncached whiteboard path.
    """
    aid = int(agent_id or 0)
    gen = int(generation)
    if aid <= 0 or gen < 0:
        return False
    return bool(_live_lock_count((_fragility_generation_consumed_lock(gen),), aid) > 0)


_LOCAL_FRAGILITY_PENDING_PACKETS: dict[int, tuple[int, ...]] = {}


def release_fragility_reservation(center_target_id: int, *, reason: str = "released") -> int:
    """Release this owner's pre-dispatch Fragility target + packet leases."""
    center = int(center_target_id or 0)
    packet = tuple(_LOCAL_FRAGILITY_PENDING_PACKETS.pop(center, ()))
    if not packet and center > 0:
        packet = tuple(_fragility_packet_ids(center) or (center,))
    _release_owned_keys((_FRAGILITY_TARGET_LOCK,), (center,))
    _release_owned_keys((_FRAGILITY_FEEDER_PENDING_LOCK,), tuple(packet))
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_FRAGILITY_RESERVATION_RELEASE",
            center_target_id=center, packet_size=len(packet), reason=str(reason),
            policy="stage1_55_failed_queue_cannot_hold_hex_cover_window",
        )
    except Exception:
        pass
    return len(packet)


def _mark_fragility_feeder_pending(center_target_id: int) -> tuple[int, ...]:
    packet = _fragility_packet_ids(center_target_id)
    _LOCAL_FRAGILITY_PENDING_PACKETS[int(center_target_id or 0)] = tuple(int(v) for v in packet)
    for enemy_id in packet:
        _try_lock(_FRAGILITY_FEEDER_PENDING_LOCK, int(enemy_id), FRAGILITY_FEEDER_PENDING_MS)
    return packet


def _fragility_feeder_publication_safe(agent_id: int) -> bool:
    """Publish Shatter authority only where pre-existing visible hexes are disposable.

    Fragility may still be useful as a condition amplifier on the whole packet;
    this filter controls only which packet members are advertised as safe Shatter
    feeders.  No visible-hex ordering is assumed.
    """
    aid = int(agent_id or 0)
    if aid <= 0 or not _valid_enemy(aid):
        return False
    visible = [int(v) for v in _mesmer_hex_ids(aid) if int(v or 0) > 0]
    if not visible:
        return True
    allowed = {int(v) for v in (Fragility_ID, Overload_ID) if int(v or 0) > 0}
    if int(Mind_Wrack_ID or 0) in visible and _mind_wrack_shatter_allowed(aid):
        allowed.add(int(Mind_Wrack_ID))
    return all(int(sid) in allowed for sid in visible)


def register_fragility_cast(
    center_target_id: int,
    *,
    packet_override: tuple[int, ...] | None = None,
) -> tuple[int, ...]:
    """Publish a generation-scoped Fragility feeder packet.

    Stage 1.53 prefers the exact dispatch-time packet captured while the shared
    hex-mutation mutex was held.  This avoids recomputing nearby geometry several
    ticks later after enemies have moved.  Older callers may omit the override.
    """
    packet = tuple(
        int(v) for v in (packet_override or ())
        if int(v or 0) > 0
    ) or _fragility_packet_ids(center_target_id)
    # The cast is now confirmed; pending-only selector leases have served their
    # purpose. The active generation below becomes the authoritative feeder.
    release_fragility_reservation(int(center_target_id), reason="cast_confirmed")
    generation = int(_fragility_generation_slot())
    active: list[int] = []
    unsafe_visible_hexes: list[int] = []
    for enemy_id in packet:
        eid = int(enemy_id)
        # Fragility applies spatially to the packet, but a feeder lease is a claim
        # that Shatter may safely remove *some* Mesmer hex.  Do not publish that
        # stronger claim where a visible pre-existing protected hex makes removal
        # ambiguous.  This changes feeder authority only, not Fragility's condition
        # amplifier value on the affected enemy.
        if not _fragility_feeder_publication_safe(eid):
            unsafe_visible_hexes.append(eid)
            continue
        _try_lock(_FRAGILITY_FEEDER_ACTIVE_LOCK, eid, FRAGILITY_FEEDER_WINDOW_MS)
        if _try_lock(_fragility_generation_active_lock(generation), eid, FRAGILITY_FEEDER_WINDOW_MS):
            active.append(eid)
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_FRAGILITY_FEEDER_ACTIVE",
            center_target_id=int(center_target_id or 0),
            packet_size=len(packet),
            active_targets=len(active),
            shatter_unsafe_visible_hex_targets=",".join(str(v) for v in unsafe_visible_hexes),
            ready_shatters=int(_ready_skill_holders_count(Shatter_Delusions_ID) or 0),
            generation=int(generation),
            policy="stage1_39_o1_generation_consumption_5s_bucket",
        )
    except Exception:
        pass
    return tuple(active)


def is_fragility_feeder_window(agent_id: int) -> bool:
    aid = int(agent_id or 0)
    if aid <= 0:
        return False
    if _is_locked(_FRAGILITY_INVALIDATED_LOCK, aid):
        return False
    if _is_locked(_FRAGILITY_FEEDER_PENDING_LOCK, aid):
        return True
    # Fast reject: nearly all enemies are outside a live Fragility packet.
    if not _is_locked(_FRAGILITY_FEEDER_ACTIVE_LOCK, aid):
        return False
    generation = _fragility_generation_for_target(aid)
    if generation < 0:
        # Compatibility fail-open to Stage 1.37 semantics if a generation marker
        # could not be observed on this client.
        return True
    return not _fragility_generation_consumed(aid, generation)


def is_fragility_feeder_active(agent_id: int) -> bool:
    aid = int(agent_id or 0)
    if aid <= 0 or not _is_locked(_FRAGILITY_FEEDER_ACTIVE_LOCK, aid):
        return False
    generation = _fragility_generation_for_target(aid)
    if generation < 0:
        return True
    return not _fragility_generation_consumed(aid, generation)


def register_shatter_delusions_cast(target_id: int, *, fragility_feeder: bool = False) -> None:
    """Consume this target once for the currently live Fragility generation."""
    aid = int(target_id or 0)
    if aid <= 0 or not fragility_feeder:
        return
    generation = _fragility_generation_for_target(aid)
    if generation < 0:
        return
    newly_consumed = bool(_try_lock(
        _fragility_generation_consumed_lock(generation), aid, FRAGILITY_FEEDER_CONSUMED_MS
    ))
    active_rows = _active_skill_target_lock_events((_FRAGILITY_FEEDER_ACTIVE_LOCK,), target_id=aid)
    active_posted = max((int(row.get("posted_at", 0) or 0) for row in active_rows), default=0)
    now = int(_now_ms() or 0)
    feeder_age_ms = max(0, now - active_posted) if now > 0 and active_posted > 0 else -1
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_FRAGILITY_FEEDER_CONSUMED",
            target_id=int(aid),
            generation=int(generation),
            newly_consumed=bool(newly_consumed),
            feeder_age_ms=int(feeder_age_ms),
            policy="stage1_66_fragility_consumption_age_generation_scoped",
        )
    except Exception:
        pass



def _optional_shatter_feeder_application(agent_id: int) -> dict[str, object]:
    """Return the newest usable concrete Web/Phantom application on one enemy."""
    aid = int(agent_id or 0)
    if aid <= 0 or not _valid_enemy(aid) or _is_locked(_OPTIONAL_FEEDER_INVALIDATED_LOCK, aid):
        return {}
    key_to_sid = {
        int(_WEB_FEEDER_ACTIVE_LOCK): int(Web_of_Disruption_ID or 0),
        int(_PHANTOM_FEEDER_ACTIVE_LOCK): int(Phantom_Pain_ID or 0),
    }
    rows = _active_skill_target_lock_events(tuple(key_to_sid.keys()), target_id=aid)
    newest_by_sid: dict[int, dict[str, int | str]] = {}
    for row in rows:
        sid = int(key_to_sid.get(int(row.get("key", 0) or 0), 0) or 0)
        posted = int(row.get("posted_at", 0) or 0)
        if sid <= 0 or not _row_matches_current_identity(aid, posted):
            continue
        previous = newest_by_sid.get(sid)
        if previous is None or posted > int(previous.get("posted_at", 0) or 0):
            newest_by_sid[sid] = row
    candidates: list[tuple[int, dict[str, int | str], int]] = []
    for sid, row in newest_by_sid.items():
        token = int(_stable_application_token(sid, aid, row) or 0)
        # Never fall back to an older row of the same skill once the newest
        # application has resolved; that older application is history too.
        if token <= 0 or _optional_application_retired(token):
            continue
        candidates.append((int(sid), row, token))
    # A live Web and live Phantom together are removal-order ambiguous.
    if len(candidates) != 1:
        return {}
    sid, row, token = candidates[0]
    visible = [int(v) for v in _shatter_known_hex_ids(aid) if int(v or 0) > 0]
    if visible == [sid]:
        return {
            "skill_id": int(sid), "target_id": aid, "token": int(token),
            "posted_at": int(row.get("posted_at", 0) or 0),
            "expires_at": int(row.get("expires_at", 0) or 0),
            "owner": str(row.get("owner", "") or ""), "provenance": "visible_or_timing_single",
        }
    if (
        sid == int(Phantom_Pain_ID or 0)
        and not visible
        and _is_locked(_PHANTOM_NATIVE_FINISHED_FEEDER_LOCK, aid)
    ):
        # Empty local detail is acceptable only while no other authoritative
        # shared setup says Shatter could consume something more valuable.
        if is_fragility_feeder_window(aid) or is_mind_wrack_active(aid) or is_mistrust_protected(aid):
            return {}
        return {
            "skill_id": int(sid), "target_id": aid, "token": int(token),
            "posted_at": int(row.get("posted_at", 0) or 0),
            "expires_at": int(row.get("expires_at", 0) or 0),
            "owner": str(row.get("owner", "") or ""), "provenance": "native_finished_effect_unobserved",
        }
    return {}


def _optional_shatter_feeder_skill(agent_id: int) -> int:
    # Normal runtime uses the exact application-aware helper above. The fallback
    # keeps this small selector independently executable for the project's older
    # isolated behavior harnesses; it is never reached in the complete module.
    helper = globals().get("_optional_shatter_feeder_application")
    if callable(helper):
        return int(helper(int(agent_id or 0)).get("skill_id", 0) or 0)
    aid = int(agent_id or 0)
    if aid <= 0 or _is_locked(_OPTIONAL_FEEDER_INVALIDATED_LOCK, aid):
        return 0
    active = []
    for key, sid in (
        (_WEB_FEEDER_ACTIVE_LOCK, int(Web_of_Disruption_ID or 0)),
        (_PHANTOM_FEEDER_ACTIVE_LOCK, int(Phantom_Pain_ID or 0)),
    ):
        if sid > 0 and _is_locked(key, aid):
            active.append(sid)
    if len(active) != 1:
        return 0
    sid = int(active[0])
    visible = [int(v) for v in _mesmer_hex_ids(aid) if int(v or 0) > 0]
    if visible == [sid]:
        return sid
    if (
        sid == int(Phantom_Pain_ID or 0)
        and not visible
        and _is_locked(_PHANTOM_NATIVE_FINISHED_FEEDER_LOCK, aid)
        and not is_fragility_feeder_window(aid)
        and not is_mind_wrack_active(aid)
        and not is_mistrust_protected(aid)
    ):
        return sid
    return 0


def _optional_application_is_current(agent_id: int, token: int) -> bool:
    app = _optional_shatter_feeder_application(int(agent_id or 0))
    return bool(app and int(app.get("token", 0) or 0) == int(token or 0))


def is_optional_shatter_feeder_window(agent_id: int) -> bool:
    return bool(_optional_shatter_feeder_skill(int(agent_id or 0)) > 0)


def is_any_shatter_feeder_window(agent_id: int) -> bool:
    return bool(is_fragility_feeder_window(int(agent_id or 0)) or is_optional_shatter_feeder_window(int(agent_id or 0)))


def is_optional_shatter_pipeline_pending(agent_id: int) -> bool:
    """True while Web/Phantom setup is already paid for but not yet converted.

    Selector-side target leases exist before native FINISHED, while the active
    feeder lease exists after completion. Treat both phases as one short
    pipeline so another Mesmer hex cannot cover the target in the dispatch
    race between setup completion and Shatter consumption.
    """
    aid = int(agent_id or 0)
    if aid <= 0:
        return False
    return bool(
        _is_locked(_WEB_TARGET_LOCK, aid)
        or _is_locked(_PHANTOM_TARGET_LOCK, aid)
        or is_optional_shatter_feeder_window(aid)
    )


def _publish_optional_shatter_feeder(
    skill_id: int, target_id: int, *, native_finished_unobserved: bool = False
) -> bool:
    sid, aid = int(skill_id or 0), int(target_id or 0)
    if aid <= 0 or sid not in {int(Web_of_Disruption_ID or 0), int(Phantom_Pain_ID or 0)}:
        return False
    if int(_ready_skill_holders_count(int(Shatter_Delusions_ID)) or 0) <= 0:
        return False
    visible = [int(v) for v in _mesmer_hex_ids(aid) if int(v or 0) > 0]
    provenance = "effect_visible"
    if native_finished_unobserved:
        # Live Stage 1.57: completed Phantom casts reached native FINISHED while
        # the target effect table stayed empty. Permit this only for Phantom and
        # only while *no* competing Mesmer hex is visible.
        if sid != int(Phantom_Pain_ID or 0) or visible or not _valid_enemy(aid):
            return False
        provenance = "native_finished_effect_unobserved"
    elif visible != [sid]:
        return False
    key = _WEB_FEEDER_ACTIVE_LOCK if sid == int(Web_of_Disruption_ID or 0) else _PHANTOM_FEEDER_ACTIVE_LOCK
    ttl = WEB_SHATTER_FEEDER_MS if sid == int(Web_of_Disruption_ID or 0) else PHANTOM_SHATTER_FEEDER_MS
    posted = bool(_try_lock(int(key), aid, int(ttl)))
    if posted and native_finished_unobserved and sid == int(Phantom_Pain_ID or 0):
        _try_lock(_PHANTOM_NATIVE_FINISHED_FEEDER_LOCK, aid, int(ttl))
    if posted:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            packet_damage, density = expected_shatter_packet_damage(aid)
            CombatDebug.log_event(
                "MESMERWAY_OPTIONAL_SHATTER_FEEDER",
                target_id=aid, feeder_skill_id=sid, density=int(density),
                expected_shatter_packet_damage=round(float(packet_damage), 1),
                provenance=str(provenance),
                effect_visible=bool(visible == [sid]),
                removal_ambiguity_guard=True,
                policy="stage1_58_phantom_native_finish_empty_effect_table_only_web_stays_strict",
            )
        except Exception:
            pass
    return posted


def active_optional_shatter_feeder_targets(*, require_spellcast_range: bool = True) -> tuple[int, ...]:
    candidates: list[int] = []
    try:
        _anchor, members = cluster_anchor_and_members()
        candidates.extend(int(v) for v in members if int(v or 0) > 0)
    except Exception:
        pass
    try:
        candidates.extend(int(v) for v in (AgentArray.GetEnemyArray() or []) if int(v or 0) > 0)
    except Exception:
        pass
    try:
        player_xy = Player.GetXY()
    except Exception:
        player_xy = None
    result: list[int] = []
    for aid in sorted(set(candidates)):
        if not _valid_enemy(aid) or _optional_shatter_feeder_skill(aid) <= 0:
            continue
        if require_spellcast_range and player_xy is not None:
            try:
                if Utils.Distance(player_xy, Agent.GetXY(aid)) > float(Range.Spellcast.value):
                    continue
            except Exception:
                continue
        result.append(int(aid))
    return tuple(result)


def reserve_optional_combo_shatter_target() -> tuple[int, int]:
    """Reserve planned Web/Phantom conversion or a safe existing Phantom finisher.

    Planned feeders may survive a canonical-packet re-anchor.  Separately, an
    already-existing sole Phantom Pain may be converted on a low-HP foe in local
    spellcast range; this does not create a fresh 2 s setup on a dying target.
    """
    rows: list[tuple[int, int, int, int, float, int]] = []
    seen: set[int] = set()
    for aid in active_optional_shatter_feeder_targets(require_spellcast_range=True):
        aid = int(aid)
        if _is_locked(_SHATTER_DELUSIONS_TARGET_LOCK, aid) or _is_locked(_HEX_MUTATION_LOCK, aid):
            continue
        sid = int(_optional_shatter_feeder_skill(aid) or 0)
        if sid <= 0:
            continue
        seen.add(aid)
        try:
            hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        except Exception:
            hp = 1.0
        if hp <= 0.03:
            continue
        packet_damage, density = expected_shatter_packet_damage(aid)
        phantom = 1 if sid == int(Phantom_Pain_ID or 0) else 0
        casting_bonus = 0
        if sid == int(Web_of_Disruption_ID or 0):
            try:
                casting_bonus = 1 if Agent.IsCasting(aid) else 0
            except Exception:
                casting_bonus = 0
        # planned feeder first, then Phantom end-effect, density and real cast bonus
        rows.append((1, phantom, int(density), int(casting_bonus), float(packet_damage), -aid))

    # Cleanup path requested for an already-present Phantom Pain even if it was
    # not placed by our setup controller and the canonical packet has moved.
    try:
        player_xy = Player.GetXY()
        enemies = AgentArray.GetEnemyArray() or []
    except Exception:
        player_xy, enemies = None, []
    for raw in enemies:
        aid = int(raw or 0)
        if aid <= 0 or aid in seen or not _valid_enemy(aid):
            continue
        if _is_locked(_SHATTER_DELUSIONS_TARGET_LOCK, aid) or _is_locked(_HEX_MUTATION_LOCK, aid):
            continue
        if player_xy is not None:
            try:
                if Utils.Distance(player_xy, Agent.GetXY(aid)) > float(Range.Spellcast.value):
                    continue
            except Exception:
                continue
        visible = [int(v) for v in _mesmer_hex_ids(aid) if int(v or 0) > 0]
        if visible != [int(Phantom_Pain_ID or 0)]:
            continue
        try:
            hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        except Exception:
            hp = 1.0
        remaining = int(_effect_remaining_ms(aid, int(Phantom_Pain_ID or 0)) or 0)
        if not (hp <= 0.22 or (0 < remaining <= 1200)):
            continue
        packet_damage, density = expected_shatter_packet_damage(aid)
        rows.append((0, 1, int(density), 0, float(packet_damage), -aid))

    rows.sort(reverse=True)
    for _planned, _phantom, _density, _casting, _damage, neg_aid in rows:
        aid = -int(neg_aid)
        sid = int(_optional_shatter_feeder_skill(aid) or 0)
        if sid <= 0:
            visible = [int(v) for v in _mesmer_hex_ids(aid) if int(v or 0) > 0]
            sid = int(Phantom_Pain_ID or 0) if visible == [int(Phantom_Pain_ID or 0)] else 0
        if sid <= 0 or not _shatter_dispatch_safe(aid):
            continue
        if _try_lock(_SHATTER_DELUSIONS_TARGET_LOCK, aid, SHATTER_DELUSIONS_RESERVATION_MS):
            return int(aid), int(sid)
    return 0, 0



def release_optional_setup_reservation(skill_id: int, target_id: int) -> None:
    sid, aid = int(skill_id or 0), int(target_id or 0)
    if aid <= 0:
        return
    if sid == int(Web_of_Disruption_ID or 0):
        _release_owned_keys((_WEB_TARGET_LOCK,), (aid,))
    elif sid == int(Phantom_Pain_ID or 0):
        _release_owned_keys((_PHANTOM_TARGET_LOCK,), (aid,))

def reserve_web_setup_target() -> int:
    """Late Web->Shatter setup: cheap packet damage, never ahead of eSurge/urgent defense."""
    if int(_ready_skill_holders_count(int(Shatter_Delusions_ID)) or 0) <= 0:
        return 0
    _anchor, members = cluster_anchor_and_members()
    rows: list[tuple[int, int, float, float, int]] = []
    for raw in members:
        aid = int(raw)
        if (not _valid_enemy(aid) or _is_locked(_WEB_TARGET_LOCK, aid)
                or _is_locked(_HEX_MUTATION_LOCK, aid) or is_any_shatter_feeder_window(aid)
                or is_interrupt_reserved(aid)):
            continue
        if _mesmer_hex_ids(aid):
            # No effect-stack ordering is available: only a clean target can become
            # an unambiguous Web feeder.
            continue
        try:
            hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        except Exception:
            hp = 1.0
        if hp <= 0.18:
            continue
        if is_mistrust_covering_cast(aid) or _is_overload_setup_protected(aid):
            continue
        packet_damage, density = expected_shatter_packet_damage(aid)
        if int(density) < int(WEB_SETUP_MIN_DENSITY) or float(packet_damage) < float(WEB_SETUP_MIN_SHATTER_PACKET_DAMAGE):
            continue
        roles = _enemy_role_scores(aid)
        role_value = int(roles.get("healer", 0)) + int(roles.get("caster", 0)) + int(roles.get("support", 0))
        try:
            casting = 1 if Agent.IsCasting(aid) else 0
        except Exception:
            casting = 0
        rows.append((int(density), int(casting), float(packet_damage), float(role_value), -aid))
    rows.sort(reverse=True)
    for _density, _casting, _damage, _role, neg_aid in rows:
        aid = -int(neg_aid)
        if _try_lock(_WEB_TARGET_LOCK, aid, WEB_TARGET_RESERVATION_MS):
            return int(aid)
    return 0


def reserve_phantom_pain_target() -> int:
    """Late Illusion setup inside the published combat group only."""
    if int(_ready_skill_holders_count(int(Shatter_Delusions_ID)) or 0) <= 0:
        return 0
    epidemic_ready = int(_ready_skill_holders_count(int(Epidemic_ID)) or 0) > 0
    _anchor, members = cluster_anchor_and_members(consumer_role="ineptitude_phantom")
    rows: list[tuple[int, int, int, int, float, int]] = []
    for raw in members:
        aid = int(raw)
        if (not _valid_hex_enemy(aid) or _is_locked(_PHANTOM_TARGET_LOCK, aid)
                or _is_locked(_HEX_MUTATION_LOCK, aid) or is_any_shatter_feeder_window(aid)):
            continue
        if _mesmer_hex_ids(aid):
            continue
        conditions = observed_condition_state(aid)
        if int(Deep_Wound_ID or 0) in conditions or _condition_presence_fallback(aid, int(Deep_Wound_ID or 0)):
            continue
        try:
            hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        except Exception:
            hp = 1.0
        # Near-dead targets are normally better served by damage already in flight.
        # 20% is deliberately not a magic execute threshold; only an even lower
        # floor rejects setup outright.
        if hp <= float(PHANTOM_MIN_SETUP_HP):
            continue
        roles = _enemy_role_scores(aid)
        healer = int(roles.get("healer", 0))
        support = int(roles.get("support", 0))
        caster = int(roles.get("caster", 0))
        neighbors = _adjacent_enemy_ids(aid)
        epidemic_receivers = 0
        if epidemic_ready:
            epidemic_receivers = sum(
                1 for rid in neighbors
                if _valid_enemy(rid)
                and not (_condition_presence_fallback(rid, int(Deep_Wound_ID or 0))
                         or int(Deep_Wound_ID or 0) in observed_condition_state(rid))
                and float(Agent.GetHealth(rid) or 0.0) > 0.12
            )
        packet_damage, density = expected_shatter_packet_damage(aid)
        important = max(healer, support, caster)
        durable = 1 if hp >= 0.45 else 0
        rows.append((int(epidemic_receivers), int(important), int(durable), int(density), float(hp), -aid))
    rows.sort(reverse=True)
    for _epi, _important, _durable, _density, _hp, neg_aid in rows:
        aid = -int(neg_aid)
        if _try_lock(_PHANTOM_TARGET_LOCK, aid, PHANTOM_TARGET_RESERVATION_MS):
            return int(aid)
    return 0


def diagnose_phantom_pain_target_selection() -> dict[str, object]:
    """Explain every Phantom candidate rejection from one combat-plan snapshot."""
    ready_shatters = int(_ready_skill_holders_count(int(Shatter_Delusions_ID)) or 0)
    plan_version = 0
    raw_members: tuple[int, ...] = ()
    try:
        from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
        plan = TeamCombatFocus.get_team_combat_plan(
            filter_range=Range.Spellcast.value, minimum_enemies=2, consumer_role="ineptitude_phantom_diag"
        )
        plan_version = int(getattr(plan, "version", 0) or 0)
        raw_members = tuple(int(v) for v in tuple(getattr(plan, "member_ids", ()) or ()))
    except Exception:
        pass
    anchor, members = cluster_anchor_and_members(consumer_role="ineptitude_phantom")
    counts = {
        "combat_group_members": int(len(raw_members)),
        "members": int(len(tuple(members or ()))),
        "out_of_range": 0, "invalid_enemy": 0, "phantom_reserved": 0,
        "hex_mutation_busy": 0, "active_shatter_feeder": 0,
        "mesmer_hex_present": 0, "deep_wound_present": 0, "low_hp": 0, "eligible": 0,
    }
    details: list[str] = []
    reachable = set(int(v) for v in members)
    for aid in raw_members:
        invalid = _hex_enemy_invalid_reason(aid)
        if invalid:
            counts["invalid_enemy"] += 1; details.append(f"{aid}:{invalid}"); continue
        if aid not in reachable:
            counts["out_of_range"] += 1; details.append(f"{aid}:out_of_spellcast_range"); continue
        if _is_locked(_PHANTOM_TARGET_LOCK, aid):
            counts["phantom_reserved"] += 1; details.append(f"{aid}:phantom_reserved"); continue
        if _is_locked(_HEX_MUTATION_LOCK, aid):
            counts["hex_mutation_busy"] += 1; details.append(f"{aid}:hex_mutation_busy"); continue
        if is_any_shatter_feeder_window(aid):
            counts["active_shatter_feeder"] += 1; details.append(f"{aid}:active_shatter_feeder"); continue
        hexes = tuple(_mesmer_hex_ids(aid))
        if hexes:
            counts["mesmer_hex_present"] += 1; details.append(f"{aid}:mesmer_hex_present[{','.join(str(x) for x in hexes)}]"); continue
        conditions = observed_condition_state(aid)
        if int(Deep_Wound_ID or 0) in conditions or _condition_presence_fallback(aid, int(Deep_Wound_ID or 0)):
            counts["deep_wound_present"] += 1; details.append(f"{aid}:deep_wound_present"); continue
        try:
            hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        except Exception:
            hp = 0.0
        if hp <= float(PHANTOM_MIN_SETUP_HP):
            counts["low_hp"] += 1; details.append(f"{aid}:low_hp[{hp:.3f}]"); continue
        counts["eligible"] += 1; details.append(f"{aid}:eligible[{hp:.3f}]")
    if ready_shatters <= 0:
        reason = "no_ready_shatter_carrier"
    elif int(counts["combat_group_members"]) <= 0:
        reason = "no_combat_group"
    elif int(counts["members"]) <= 0 and int(counts["out_of_range"]) > 0:
        reason = "combat_group_out_of_range"
    elif int(counts["members"]) <= 0:
        reason = "no_cluster_members"
    elif int(counts["eligible"]) > 0:
        reason = "reservation_race_or_selector_recheck"
    else:
        ranked = [(int(counts[k]), k) for k in (
            "mesmer_hex_present", "active_shatter_feeder", "hex_mutation_busy", "out_of_range",
            "deep_wound_present", "phantom_reserved", "low_hp", "invalid_enemy"
        )]
        ranked.sort(reverse=True)
        reason = str(ranked[0][1]) if ranked and ranked[0][0] > 0 else "no_eligible_target"
    return {
        "reason": reason, "ready_shatters": ready_shatters, "anchor_id": int(anchor),
        "plan_version": int(plan_version), "combat_group_ids": ",".join(str(v) for v in raw_members),
        "candidate_rejections": ";".join(details), **counts,
    }

def _condition_recipient_value(source_state: dict[int, int], recipient_id: int, *, served: bool) -> tuple[float, tuple[int, ...], tuple[int, ...], bool]:
    """Return incremental Epidemic value; refreshes are worth less than new conditions."""
    rid = int(recipient_id or 0)
    current = observed_condition_state(rid)
    roles = _enemy_role_scores(rid)
    martial = _enemy_is_martial(rid)
    fragility = False
    try:
        fragility = bool(Routines.Checks.Agents.HasEffect(rid, int(Fragility_ID)))
    except Exception:
        fragility = False
    total = 0.0
    new_ids: list[int] = []
    extended_ids: list[int] = []
    important = False
    for sid, source_remaining in source_state.items():
        sid = int(sid)
        source_remaining = int(source_remaining or 0)
        if source_remaining < int(EPIDEMIC_MIN_CONDITION_REMAINING_MS):
            continue
        base = float(_CONDITION_BASE_VALUE.get(sid, 1.0) or 1.0)
        duration_factor = max(0.35, min(1.0, float(source_remaining) / 5000.0))
        existing = sid in current
        recipient_remaining = int(current.get(sid, 0) or 0)
        if not existing:
            gain = base * duration_factor
            if sid == int(Deep_Wound_ID or 0) and int(roles.get("healer", 0)) >= 70:
                gain += 3.0; important = True
            if sid == int(Dazed_ID or 0) and max(int(roles.get("caster", 0)), int(roles.get("healer", 0))) >= 70:
                gain += 3.5; important = True
            if sid in {int(Blind_ID or 0), int(Weakness_ID or 0)} and martial:
                gain += 2.0
            if fragility:
                # Only a genuinely new condition is credited with a Fragility event.
                gain += 1.5
            total += gain
            new_ids.append(sid)
        elif source_remaining >= recipient_remaining + 1500:
            # Refresh/extension may be useful, but it is not counted as a fresh
            # condition and receives no guaranteed Fragility credit.
            total += 0.20 * base * duration_factor
            extended_ids.append(sid)
    if served:
        total *= 0.15
        important = False
        new_ids = []
    return float(total), tuple(new_ids), tuple(extended_ids), bool(important)


def epidemic_target_plan(*, phantom_only: bool = False) -> dict[str, object]:
    """Best current condition source inside the published combat group.

    ``phantom_only`` is the Stage 1.61 time-critical pass used immediately after
    primary eSurge.  It never promotes ordinary condition spreading.
    """
    _anchor, members = cluster_anchor_and_members(consumer_role="epidemic")
    rows: list[tuple[float, int, int, float, int, dict[str, object]]] = []
    for raw in members:
        source = int(raw)
        if not _valid_enemy(source) or _is_locked(_EPIDEMIC_SOURCE_LOCK, source):
            continue
        try:
            hp = max(0.0, float(Agent.GetHealth(source) or 0.0))
        except Exception:
            hp = 0.0
        phantom_remote = bool(_is_locked(_PHANTOM_DEEP_WOUND_SOURCE_LOCK, source))
        phantom_expected = bool(_is_locked(_PHANTOM_DEEP_WOUND_EXPECTED_LOCK, source))
        phantom_provenance = bool(phantom_remote or phantom_expected)
        if bool(phantom_only) and not phantom_provenance:
            continue
        if hp <= float(EPIDEMIC_SOURCE_MIN_HP):
            if not (phantom_provenance and hp > float(EPIDEMIC_PHANTOM_SOURCE_MIN_HP)):
                continue
        source_state = observed_condition_state(source)
        source_provenance = "observed" if source_state else "none"
        if int(Deep_Wound_ID or 0) not in source_state:
            if phantom_remote:
                source_state[int(Deep_Wound_ID)] = max(
                    int(EPIDEMIC_MIN_CONDITION_REMAINING_MS),
                    _lock_remaining_ms(_PHANTOM_DEEP_WOUND_SOURCE_LOCK, source),
                )
                source_provenance = "phantom_end_observed_remote"
            elif phantom_expected:
                source_state[int(Deep_Wound_ID)] = max(
                    int(EPIDEMIC_MIN_CONDITION_REMAINING_MS),
                    _lock_remaining_ms(_PHANTOM_DEEP_WOUND_EXPECTED_LOCK, source),
                )
                source_provenance = "phantom_end_expected_unobserved"
        min_source_hp = float(EPIDEMIC_PHANTOM_SOURCE_MIN_HP if phantom_provenance else EPIDEMIC_SOURCE_MIN_HP)
        source_state = {
            int(sid): int(rem) for sid, rem in source_state.items()
            if int(rem or 0) >= int(EPIDEMIC_MIN_CONDITION_REMAINING_MS)
        }
        if not source_state:
            continue
        signature = int(_condition_signature(source_state))
        if signature <= 0:
            continue
        recipient_rows: list[tuple[float, int, tuple[int, ...], tuple[int, ...], bool]] = []
        for rid in _adjacent_enemy_ids(source):
            if not _valid_enemy(rid):
                continue
            try:
                if float(Agent.GetHealth(rid) or 0.0) <= 0.10:
                    continue
            except Exception:
                continue
            served = bool(_is_locked(_epidemic_recipient_signature_lock(signature), rid))
            value, new_ids, extended_ids, important = _condition_recipient_value(source_state, rid, served=served)
            if value > 0.0:
                recipient_rows.append((float(value), int(rid), tuple(new_ids), tuple(extended_ids), bool(important)))
        if not recipient_rows:
            continue
        recipient_rows.sort(reverse=True)
        relevant = [row for row in recipient_rows if row[2] and row[0] >= 1.5]
        single_important = len(relevant) == 1 and bool(relevant[0][4]) and float(relevant[0][0]) >= float(EPIDEMIC_SINGLE_IMPORTANT_SCORE)
        if len(relevant) < int(EPIDEMIC_MIN_NEW_RECIPIENTS) and not single_important:
            continue
        total = float(sum(row[0] for row in recipient_rows))
        phantom_source = 1 if (
            _is_locked(_PHANTOM_DEEP_WOUND_SOURCE_LOCK, source)
            or _is_locked(_PHANTOM_DEEP_WOUND_EXPECTED_LOCK, source)
        ) else 0
        plan = {
            "source_id": int(source),
            "source_hp": float(hp),
            "source_state": dict(source_state),
            "signature": int(signature),
            "recipient_rows": tuple(recipient_rows),
            "recipient_ids": tuple(int(row[1]) for row in recipient_rows if row[2]),
            "relevant_recipient_count": int(len(relevant)),
            "single_important_exception": bool(single_important),
            "expected_score": float(total),
            "reason": "phantom_deep_wound_source" if phantom_source else "existing_condition_packet",
            "source_condition_provenance": str(source_provenance),
            "source_min_hp": float(min_source_hp),
        }
        rows.append((float(total), int(phantom_source), int(len(relevant)), float(hp), -source, plan))
    rows.sort(reverse=True, key=lambda row: row[:5])
    return dict(rows[0][-1]) if rows else {}


def diagnose_epidemic_target_selection() -> dict[str, object]:
    """Explain why no Epidemic plan exists without changing its value gates."""
    _anchor, members = cluster_anchor_and_members(consumer_role="epidemic_diag")
    counts = {
        "members": int(len(tuple(members or ()))),
        "invalid_source": 0,
        "source_reserved": 0,
        "source_low_hp": 0,
        "no_observed_conditions": 0,
        "confirmed_no_conditions": 0,
        "condition_data_unknown": 0,
        "conditions_too_short": 0,
        "no_adjacent_recipient": 0,
        "no_incremental_value": 0,
        "insufficient_new_recipients": 0,
        "phantom_expected_sources": 0,
        "phantom_remote_observed_sources": 0,
        "eligible_sources": 0,
    }
    best_relevant = 0
    best_score = 0.0
    for raw in members:
        source = int(raw or 0)
        if not _valid_enemy(source):
            counts["invalid_source"] += 1
            continue
        if _is_locked(_EPIDEMIC_SOURCE_LOCK, source):
            counts["source_reserved"] += 1
            continue
        try:
            hp = max(0.0, float(Agent.GetHealth(source) or 0.0))
        except Exception:
            hp = 0.0
        phantom_remote = bool(_is_locked(_PHANTOM_DEEP_WOUND_SOURCE_LOCK, source))
        phantom_expected = bool(_is_locked(_PHANTOM_DEEP_WOUND_EXPECTED_LOCK, source))
        phantom_provenance = bool(phantom_remote or phantom_expected)
        if phantom_expected:
            counts["phantom_expected_sources"] += 1
        if phantom_remote:
            counts["phantom_remote_observed_sources"] += 1
        if hp <= float(EPIDEMIC_SOURCE_MIN_HP):
            if not (phantom_provenance and hp > float(EPIDEMIC_PHANTOM_SOURCE_MIN_HP)):
                counts["source_low_hp"] += 1
                continue
        observation = condition_observation_status(source)
        raw_state, source_provenance = epidemic_source_condition_state(source)
        if not raw_state:
            status = str(observation.get("status", "condition_state_unknown"))
            if status == "native_no_condition":
                counts["confirmed_no_conditions"] += 1
            else:
                counts["condition_data_unknown"] += 1
            counts["no_observed_conditions"] += 1
            continue
        source_state = {
            int(sid): int(rem) for sid, rem in raw_state.items()
            if int(rem or 0) >= int(EPIDEMIC_MIN_CONDITION_REMAINING_MS)
        }
        if not source_state:
            counts["conditions_too_short"] += 1
            continue
        adjacent = [int(rid) for rid in _adjacent_enemy_ids(source) if _valid_enemy(int(rid))]
        if not adjacent:
            counts["no_adjacent_recipient"] += 1
            continue
        signature = int(_condition_signature(source_state))
        recipient_rows = []
        for rid in adjacent:
            try:
                if float(Agent.GetHealth(rid) or 0.0) <= 0.10:
                    continue
            except Exception:
                continue
            served = bool(signature > 0 and _is_locked(_epidemic_recipient_signature_lock(signature), rid))
            value, new_ids, extended_ids, important = _condition_recipient_value(
                source_state, rid, served=served
            )
            if float(value) > 0.0:
                recipient_rows.append((float(value), int(rid), tuple(new_ids), tuple(extended_ids), bool(important)))
        if not recipient_rows:
            counts["no_incremental_value"] += 1
            continue
        relevant = [row for row in recipient_rows if row[2] and float(row[0]) >= 1.5]
        single_important = (
            len(relevant) == 1
            and bool(relevant[0][4])
            and float(relevant[0][0]) >= float(EPIDEMIC_SINGLE_IMPORTANT_SCORE)
        )
        best_relevant = max(best_relevant, int(len(relevant)))
        best_score = max(best_score, float(sum(row[0] for row in recipient_rows)))
        if len(relevant) < int(EPIDEMIC_MIN_NEW_RECIPIENTS) and not single_important:
            counts["insufficient_new_recipients"] += 1
            continue
        counts["eligible_sources"] += 1
    if int(counts["members"]) <= 0:
        reason = "no_cluster_members"
    elif int(counts["eligible_sources"]) > 0:
        reason = "reservation_race_or_selector_recheck"
    else:
        # no_observed_conditions is an umbrella counter, not a diagnosis.
        # Prefer concrete missing-data vs confirmed-absence reasons.
        priority = (
            "condition_data_unknown", "confirmed_no_conditions",
            "conditions_too_short", "insufficient_new_recipients",
            "no_incremental_value", "no_adjacent_recipient",
            "source_reserved", "source_low_hp", "invalid_source"
        )
        ranked = [(int(counts[k]), -idx, k) for idx, k in enumerate(priority)]
        ranked.sort(reverse=True)
        reason = str(ranked[0][2]) if ranked and ranked[0][0] > 0 else "no_eligible_source"
    return {
        "reason": reason,
        "best_relevant_recipients": int(best_relevant),
        "best_expected_score": round(float(best_score), 2),
        **counts,
    }


def epidemic_plan_still_valid(plan: dict[str, object]) -> bool:
    """Cheap last-moment revalidation before dispatching an already reserved Epidemic.

    Queue ownership is not proof that the source still carries useful conditions or
    that the planned recipients are still adjacent.  Fail closed for the optional
    spread without disturbing normal damage/interrupt rotation.
    """
    source = int(plan.get("source_id", 0) or 0)
    if source <= 0 or not _valid_enemy(source):
        return False
    try:
        source_hp = float(Agent.GetHealth(source) or 0.0)
    except Exception:
        return False
    source_state = observed_condition_state(source)
    phantom_remote_lock = int(globals().get("_PHANTOM_DEEP_WOUND_SOURCE_LOCK", 0) or 0)
    phantom_expected_lock = int(globals().get("_PHANTOM_DEEP_WOUND_EXPECTED_LOCK", 0) or 0)
    phantom_remote = bool(phantom_remote_lock > 0 and _is_locked(phantom_remote_lock, source))
    phantom_expected = bool(phantom_expected_lock > 0 and _is_locked(phantom_expected_lock, source))
    phantom_provenance = bool(phantom_remote or phantom_expected)
    deep_wound_id = int(globals().get("Deep_Wound_ID", 0) or 0)
    if deep_wound_id > 0 and deep_wound_id not in source_state:
        if phantom_remote:
            remaining_fn = globals().get("_lock_remaining_ms")
            remaining = int(remaining_fn(phantom_remote_lock, source) or 0) if callable(remaining_fn) else 0
            source_state[deep_wound_id] = max(
                int(globals().get("EPIDEMIC_MIN_CONDITION_REMAINING_MS", 650) or 650),
                remaining,
            )
        elif phantom_expected:
            remaining_fn = globals().get("_lock_remaining_ms")
            remaining = int(remaining_fn(phantom_expected_lock, source) or 0) if callable(remaining_fn) else 0
            source_state[deep_wound_id] = max(
                int(globals().get("EPIDEMIC_MIN_CONDITION_REMAINING_MS", 650) or 650),
                remaining,
            )
    min_source_hp = float(
        globals().get("EPIDEMIC_PHANTOM_SOURCE_MIN_HP", 0.10)
        if phantom_provenance else 0.10
    )
    if source_hp <= min_source_hp:
        return False
    source_state = {
        int(sid): int(rem) for sid, rem in source_state.items()
        if int(rem or 0) >= int(EPIDEMIC_MIN_CONDITION_REMAINING_MS)
    }
    if not source_state:
        return False
    adjacent = set(_adjacent_enemy_ids(source))
    useful = 0
    important_exception = False
    for row in tuple(plan.get("recipient_rows", ()) or ()):
        _old_value, rid, _old_new, _old_extended, _old_important = row
        rid = int(rid or 0)
        if rid <= 0 or rid not in adjacent or not _valid_enemy(rid):
            continue
        try:
            if float(Agent.GetHealth(rid) or 0.0) <= 0.10:
                continue
        except Exception:
            continue
        signature = int(_condition_signature(source_state))
        served = bool(signature > 0 and _is_locked(_epidemic_recipient_signature_lock(signature), rid))
        value, new_ids, _extended, important = _condition_recipient_value(source_state, rid, served=served)
        if new_ids and float(value) >= 1.5:
            useful += 1
            if important and float(value) >= float(EPIDEMIC_SINGLE_IMPORTANT_SCORE):
                important_exception = True
    return bool(useful >= int(EPIDEMIC_MIN_NEW_RECIPIENTS) or (useful == 1 and important_exception))


def reserve_epidemic_target(*, phantom_only: bool = False) -> dict[str, object]:
    """Reserve one source and its useful adjacent recipients across all carriers."""
    plan = epidemic_target_plan(phantom_only=bool(phantom_only))
    source = int(plan.get("source_id", 0) or 0)
    if source <= 0:
        return {}
    if not _try_lock(_EPIDEMIC_SOURCE_LOCK, source, EPIDEMIC_SOURCE_RESERVATION_MS):
        return {}
    acquired: list[int] = []
    useful_rows = []
    for row in tuple(plan.get("recipient_rows", ()) or ()):
        value, rid, new_ids, extended_ids, important = row
        if not tuple(new_ids):
            continue
        if _try_lock(_EPIDEMIC_RECIPIENT_PENDING_LOCK, int(rid), EPIDEMIC_RECIPIENT_PENDING_MS):
            acquired.append(int(rid)); useful_rows.append(row)
    relevant = [row for row in useful_rows if row[2] and float(row[0]) >= 1.5]
    single_important = len(relevant) == 1 and bool(relevant[0][4]) and float(relevant[0][0]) >= float(EPIDEMIC_SINGLE_IMPORTANT_SCORE)
    if len(relevant) < int(EPIDEMIC_MIN_NEW_RECIPIENTS) and not single_important:
        _release_owned_keys((_EPIDEMIC_SOURCE_LOCK,), (source,))
        _release_owned_keys((_EPIDEMIC_RECIPIENT_PENDING_LOCK,), tuple(acquired))
        return {}
    plan["recipient_rows"] = tuple(useful_rows)
    plan["recipient_ids"] = tuple(int(row[1]) for row in useful_rows if row[2])
    plan["relevant_recipient_count"] = int(len(relevant))
    plan["single_important_exception"] = bool(single_important)
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_EPIDEMIC_PLAN",
            source_id=source,
            expected_recipients=int(len(plan["recipient_ids"])),
            recipient_ids=",".join(str(int(v)) for v in plan["recipient_ids"]),
            relevant_recipients=int(len(relevant)),
            single_important_exception=bool(single_important),
            expected_score=round(float(plan.get("expected_score", 0.0) or 0.0), 2),
            reason=str(plan.get("reason", "")),
            source_condition_provenance=str(plan.get("source_condition_provenance", "observed")),
            source_min_hp=round(float(plan.get("source_min_hp", EPIDEMIC_SOURCE_MIN_HP) or EPIDEMIC_SOURCE_MIN_HP), 3),
            policy="stage1_59_phantom_expected_condition_provenance_plus_observed_conditions",
        )
    except Exception:
        pass
    return plan


def release_epidemic_reservation(plan: dict[str, object], *, reason: str = "released") -> None:
    source = int(plan.get("source_id", 0) or 0)
    recipients = tuple(int(v) for v in (plan.get("recipient_ids", ()) or ()) if int(v or 0) > 0)
    if source > 0:
        _release_owned_keys((_EPIDEMIC_SOURCE_LOCK,), (source,))
    if recipients:
        _release_owned_keys((_EPIDEMIC_RECIPIENT_PENDING_LOCK,), recipients)
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event("MESMERWAY_EPIDEMIC_RELEASE", source_id=source, reason=str(reason))
    except Exception:
        pass


def register_epidemic_cast(plan: dict[str, object]) -> dict[str, object]:
    """Observe actual recipient conditions after a finished Epidemic cast."""
    source = int(plan.get("source_id", 0) or 0)
    signature = int(plan.get("signature", 0) or 0)
    rows = tuple(plan.get("recipient_rows", ()) or ())
    observed: list[int] = []
    condition_names: set[str] = set()
    for _value, rid, new_ids, extended_ids, _important in rows:
        rid = int(rid)
        if not _valid_enemy(rid):
            continue
        state = observed_condition_state(rid)
        expected = tuple(int(v) for v in tuple(new_ids) + tuple(extended_ids))
        if expected and any(int(sid) in state for sid in expected):
            observed.append(rid)
            for sid in expected:
                if int(sid) in state:
                    condition_names.add(str(_CONDITION_NAME_BY_ID.get(int(sid), int(sid))))
            _try_lock(_epidemic_recipient_signature_lock(signature), rid, EPIDEMIC_SERVED_SIGNATURE_MS)
    release_epidemic_reservation(plan, reason="cast_finished")
    record = {
        "source_id": source,
        "expected_recipients": int(len(tuple(plan.get("recipient_ids", ()) or ()))),
        "observed_recipients": int(len(observed)),
        "observed_recipient_ids": tuple(observed),
        "conditions": tuple(sorted(condition_names)),
        "reason": str(plan.get("reason", "")),
        "source_condition_provenance": str(plan.get("source_condition_provenance", "observed")),
    }
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_EPIDEMIC_CAST",
            source_id=source,
            expected_recipients=int(record["expected_recipients"]),
            expected_recipient_ids=",".join(str(int(v)) for v in tuple(plan.get("recipient_ids", ()) or ())),
            observed_recipients=int(record["observed_recipients"]),
            observed_recipient_ids=",".join(str(int(v)) for v in tuple(record.get("observed_recipient_ids", ()) or ())),
            conditions=",".join(record["conditions"]),
            reason=str(record["reason"]),
            source_condition_provenance=str(record.get("source_condition_provenance", "observed")),
            policy="stage1_59_native_finished_epidemic_with_source_provenance",
        )
    except Exception:
        pass
    return record


def register_optional_combo_shatter(
    target_id: int, feeder_skill_id: int, *,
    application_token: int = 0,
    application_posted_at: int = 0,
    hex_lease_posted_at: int = 0,
    web_casting_before_shatter: bool | None = None,
    deep_wound_before_shatter: bool | None = None,
) -> dict[str, object]:
    """Record observed end-effect facts; never invent Web interrupt or Deep Wound."""
    aid, sid = int(target_id or 0), int(feeder_skill_id or 0)
    record: dict[str, object] = {"target_id": aid, "feeder_skill_id": sid}
    if aid <= 0 or sid <= 0:
        return record
    if sid == int(Phantom_Pain_ID or 0):
        deep_wound = bool(_condition_presence_fallback(aid, int(Deep_Wound_ID or 0)))
        try:
            deep_wound = deep_wound or bool(Routines.Checks.Agents.HasEffect(aid, int(Deep_Wound_ID)))
        except Exception:
            pass
        remaining = int(_effect_remaining_ms(aid, int(Deep_Wound_ID)) or 0) if deep_wound else 0
        newly_observed = bool(deep_wound and not bool(deep_wound_before_shatter))
        record.update({
            "deep_wound_observed": bool(deep_wound),
            "deep_wound_new_after_shatter": bool(newly_observed),
            "deep_wound_remaining_ms": int(remaining),
        })
        # Native FINISHED Shatter on an unambiguous Phantom feeder is deterministic
        # end-effect provenance even if this account cannot see Deep Wound.  Keep
        # observation and expectation distinct: Epidemic may use the short expected
        # lease, while telemetry never labels it as observed.
        _try_lock(_PHANTOM_DEEP_WOUND_EXPECTED_LOCK, aid, PHANTOM_DEEP_WOUND_EXPECTED_SOURCE_MS)
        if newly_observed:
            _try_lock(_PHANTOM_DEEP_WOUND_SOURCE_LOCK, aid, PHANTOM_DEEP_WOUND_SOURCE_MS)
        record["deep_wound_expected_from_phantom_end"] = True
        record["deep_wound_provenance"] = "observed" if deep_wound else "phantom_end_expected_unobserved"
    elif sid == int(Web_of_Disruption_ID or 0):
        # This is only an observed opportunity immediately before Shatter was
        # dispatched. It is deliberately NOT labelled as a successful interrupt.
        record["web_second_interrupt_opportunity"] = bool(web_casting_before_shatter)
        try:
            record["web_hex_removed_observed"] = int(Web_of_Disruption_ID or 0) not in _effect_ids(aid)
        except Exception:
            record["web_hex_removed_observed"] = False
    removal_observed = bool(
        record.get("deep_wound_new_after_shatter", False)
        or record.get("web_hex_removed_observed", False)
    )
    token = int(application_token or 0)
    if token > 0:
        _retire_optional_application(
            token, target_id=aid, skill_id=sid,
            reason="native_finished_optional_shatter_resolution",
            removal_observed=bool(removal_observed),
        )
    # Invalidate only the timing evidence captured for the concrete application.
    # Native FINISHED makes the old lifetime unsafe evidence after this resolved
    # mutation, but removal_observed remains a separate fact.
    cutoff = int(hex_lease_posted_at or application_posted_at or 0)
    if cutoff > 0:
        _invalidate_hex_lifetime_evidence(
            aid, sid, cutoff, reason="optional_shatter_application_resolved",
            removal_observed=bool(removal_observed),
        )
    record["application_token"] = int(token)
    record["application_retired"] = bool(token > 0)
    record["hex_removal_observed"] = bool(removal_observed)
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_OPTIONAL_SHATTER_END_EFFECT",
            target_id=aid,
            feeder_skill_id=sid,
            deep_wound_observed=bool(record.get("deep_wound_observed", False)),
            deep_wound_new_after_shatter=bool(record.get("deep_wound_new_after_shatter", False)),
            deep_wound_remaining_ms=int(record.get("deep_wound_remaining_ms", 0) or 0),
            deep_wound_expected_from_phantom_end=bool(record.get("deep_wound_expected_from_phantom_end", False)),
            deep_wound_provenance=str(record.get("deep_wound_provenance", "none")),
            web_second_interrupt_opportunity=bool(record.get("web_second_interrupt_opportunity", False)),
            web_hex_removed_observed=bool(record.get("web_hex_removed_observed", False)),
            policy="stage1_45_observed_effect_not_assumed",
        )
    except Exception:
        pass
    return record

def reserve_mind_wrack_target() -> int:
    """Reserve Mind Wrack with Stage 1.63 Aneurysm pre-cast priority.

    A pending Aneurysm transaction owns the highest Wrack priority: other
    eSurge Mesmers plant Wrack on adjacent foes that the upcoming Aneurysm
    pulse is expected to drive to zero Energy.  The refilled anchor itself is
    deliberately *not* Wracked after Aneurysm; eSurge consumes that fresh
    battery immediately.  Outside a pending reset, the established dense/two-
    eSurge-zero-window policy remains unchanged.
    """
    if int(Mind_Wrack_ID or 0) <= 0:
        return 0

    # Stage 1.63: consume shared pre-Aneurysm setup requests before generic
    # battery setup.  A target is revalidated here because Energy and position
    # may have changed since the Aneurysm carrier published the plan.
    for anchor_id in active_aneurysm_precast_anchor_ids():
        status = aneurysm_precast_status(int(anchor_id))
        expected_loss = float(status.get("expected_neighbor_loss", 0.0) or 0.0)
        for aid in tuple(status.get("desired_targets", ()) or ()):
            aid = int(aid or 0)
            if aid <= 0 or not _valid_enemy(aid) or not _spellcast_reachable(aid):
                continue
            # Stage 1.65: a shared lifetime lease is enough to suppress a
            # duplicate pre-Aneurysm Wrack cast.  The Aneurysm carrier already
            # accepts that lease as readiness, so another Mesmer should not
            # spend a cast merely because its local effect list cannot see the
            # remote owner's Hex.
            if (is_mind_wrack_active(aid)
                    or _hex_lifetime_remaining_ms(aid, int(Mind_Wrack_ID)) > 0
                    or _is_locked(_MIND_WRACK_CAST_LOCK, aid)):
                continue
            if is_fragility_feeder_window(aid) or is_optional_shatter_pipeline_pending(aid):
                continue
            try:
                hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
            except Exception:
                hp = 0.0
            if hp <= float(MIND_WRACK_MIN_TARGET_HP):
                continue
            projected = float(estimate_enemy_projected_energy(aid) or 0.0)
            # Wrack is useful for this combo only when Aneurysm can actually
            # cross the zero-Energy boundary.  Do not waste it on already-empty
            # foes or on neighbours above the pulse's drain cap.
            if projected <= 0.5 or projected > expected_loss + 0.5:
                continue
            if _try_lock(_MIND_WRACK_CAST_LOCK, aid, MIND_WRACK_PENDING_MS):
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "MESMERWAY_MIND_WRACK_PRE_ANEURYSM_PRIORITY",
                        target_id=int(aid),
                        aneurysm_anchor_id=int(anchor_id),
                        projected_energy=f"{float(projected):.1f}",
                        expected_aneurysm_drain=f"{float(expected_loss):.1f}",
                        policy="stage1_63_precast_zeroable_neighbor_before_aneurysm",
                    )
                except Exception:
                    pass
                return int(aid)

    _anchor, members = cluster_anchor_and_members()
    if not members:
        return 0
    primary_focus, secondary_focus = _team_damage_focus_pair(consumer_role="mind_wrack_two_anchor_pair")
    # A freshly refilled Aneurysm anchor is now reserved for immediate eSurge
    # consumption, not a post-reset Wrack setup.
    active_anchors = set(int(v) for v in active_aneurysm_anchor_ids())
    rows: list[tuple[int, int, int, int, float, int, int, float, int]] = []
    for aid in members:
        aid = int(aid)
        if not _valid_enemy(aid) or aid in active_anchors:
            continue
        if is_mind_wrack_active(aid) or _is_locked(_MIND_WRACK_CAST_LOCK, aid):
            continue
        if is_fragility_feeder_window(aid) or is_optional_shatter_pipeline_pending(aid):
            continue
        try:
            hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        except Exception:
            hp = 0.0
        if hp <= float(MIND_WRACK_MIN_TARGET_HP):
            continue
        metrics = esurge_target_metrics(aid)
        projected = float(metrics.get("projected_energy", 0.0) or 0.0)
        if projected <= 0.5:
            continue
        density = int(metrics.get("density", 1) or 1)
        drain = max(1.0, float(metrics.get("drain_cap", current_esurge_drain()) or current_esurge_drain()))
        if projected < max(4.0, float(MIND_WRACK_MIN_DRAIN_FRACTION) * drain):
            continue
        if projected > float(MIND_WRACK_MAX_ESURGES_TO_ZERO) * drain:
            continue
        max_energy = float(estimate_enemy_max_energy(aid))
        caster = threat = 0
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            scores = EnemyKnowledge.role_scores(aid)
            caster = int(scores.get("caster", 0))
            threat = int(EnemyKnowledge.threat_bonus(aid))
        except Exception:
            pass
        if density < 2 and caster < 60:
            continue
        # Stage 2.24 log-driven tuning: Wrack only pays its large burst when
        # Energy reaches zero. Within the existing dense/two-eSurge eligibility
        # window, prefer a target the *next* eSurge can zero; for equal density
        # prefer lower projected Energy instead of the old higher-Energy order.
        zeroable_next_esurge = 1 if projected <= drain + 0.5 else 0
        rows.append((
            2 if aid == primary_focus else 1 if aid == secondary_focus else 0,
            int(zeroable_next_esurge), int(density), -float(projected), float(max_energy),
            int(caster), int(threat), float(hp), -aid,
        ))
    rows.sort(reverse=True)
    for _focus_rank, _zeroable_next, _density, _neg_projected, _max_energy, _caster, _threat, _hp, neg_aid in rows:
        aid = -int(neg_aid)
        if _try_lock(_MIND_WRACK_CAST_LOCK, aid, MIND_WRACK_PENDING_MS):
            return int(aid)
    return 0


def register_mind_wrack_cast(target_id: int) -> None:
    aid = int(target_id or 0)
    if aid <= 0:
        return
    _try_lock(_MIND_WRACK_ACTIVE_FALLBACK_LOCK, aid, MIND_WRACK_ACTIVE_FALLBACK_MS)
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        _metrics = esurge_target_metrics(aid)
        CombatDebug.log_event(
            "MESMERWAY_MIND_WRACK_ASSIGNED",
            target_id=int(aid),
            aneurysm_anchor=bool(_is_locked(_ANEURYSM_REFILLED_LOCK, aid)),
            projected_energy=f"{float(_metrics.get('projected_energy', 0.0) or 0.0):.1f}",
            zeroable_next_esurge=bool(float(_metrics.get("mind_wrack_zero_bonus", 0.0) or 0.0) > 0.0),
            expected_zero_bonus=f"{float(_metrics.get('mind_wrack_zero_bonus', 0.0) or 0.0):.1f}",
            policy="stage2_24_prefer_next_esurge_zero_transition_for_wrack",
        )
    except Exception:
        pass


def _mesmer_hex_ids(agent_id: int) -> list[int]:
    result: list[int] = []
    try:
        for sid in _effect_ids(int(agent_id)):
            if sid <= 0 or not GLOBAL_CACHE.Skill.Flags.IsHex(int(sid)):
                continue
            profession_id, _secondary = GLOBAL_CACHE.Skill.GetProfession(int(sid))
            if int(getattr(profession_id, "value", profession_id) or 0) != int(Profession.Mesmer.value):
                continue
            if int(sid) not in result:
                result.append(int(sid))
    except Exception:
        return []
    return result


def _protected_mesmer_hex_ids(agent_id: int) -> set[int]:
    """Hexes Shatter Delusions must never consume while their setup is useful."""
    aid = int(agent_id or 0)
    protected = {
        int(v) for v in (
            Mistrust_ID, Frustration_ID, Arcane_Conundrum_ID, Ineptitude_ID,
            Wandering_Eye_ID, Clumsiness_ID, Fragility_ID, Panic_ID,
            Web_of_Disruption_ID, Phantom_Pain_ID, Overload_ID,
        ) if int(v or 0) > 0
    }
    # Mind Wrack is protected by default. It becomes expendable only in the
    # explicit salvage/finisher/dense-conversion cases handled below.
    if is_mind_wrack_active(aid):
        protected.add(int(Mind_Wrack_ID))
    return protected



def _overload_removal_acceptable_for_fragility_feeder(agent_id: int) -> bool:
    """Allow Overload only after its protected value window is spent.

    The short setup/landing protection always wins. Once active, Overload becomes
    expendable only at the same low-HP/near-expiry boundary used by the normal
    single-hex salvage path. This never relaxes Mistrust/Frustration/etc.
    """
    aid = int(agent_id or 0)
    if aid <= 0 or int(Overload_ID or 0) <= 0:
        return False
    if _is_overload_setup_protected(aid):
        return False
    remaining = int(_hex_lifetime_remaining_ms(aid, int(Overload_ID)) or 0)
    if remaining <= 0:
        return False
    try:
        hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
    except Exception:
        hp = 1.0
    return bool(hp <= 0.18 or remaining <= int(SHATTER_SALVAGE_OVERLOAD_MS))

def _fragility_feeder_dispatch_safe(agent_id: int) -> bool:
    """Fail closed on visible multi-hex ambiguity without inventing removal order.

    The shared feeder lease is authoritative proof that Fragility was applied even
    when an ally client's effect cache omits it.  It is *not* proof that Fragility
    would be the hex removed when other Mesmer hexes are visible.  Therefore every
    visible alternative must itself be safe to consume before a feeder Shatter is
    admitted.
    """
    aid = int(agent_id or 0)
    if aid <= 0 or not is_fragility_shatter_feeder_target(aid):
        return False
    visible = [int(v) for v in _shatter_known_hex_ids(aid) if int(v or 0) > 0]
    if not visible:
        # Cross-client caches often omit ally-owned Fragility entirely.  The
        # authoritative feeder lease plus mutation mutex is the only proof here.
        return True
    protected = set(_protected_mesmer_hex_ids(aid))
    protected.discard(int(Fragility_ID or 0))
    allowed = {int(Fragility_ID)} if int(Fragility_ID or 0) > 0 else set()
    if int(Overload_ID or 0) in visible and _overload_removal_acceptable_for_fragility_feeder(aid):
        protected.discard(int(Overload_ID))
        allowed.add(int(Overload_ID))
    if int(Mind_Wrack_ID or 0) > 0 and _mind_wrack_shatter_allowed(aid):
        protected.discard(int(Mind_Wrack_ID))
        allowed.add(int(Mind_Wrack_ID))
    # Optional planned Web/Phantom are never mixed into this feeder path: when
    # either is visible alongside Fragility, the actual removed hex is ambiguous.
    for sid in visible:
        if int(sid) in protected or int(sid) not in allowed:
            return False
    return True


def is_fragility_shatter_feeder_target(agent_id: int, hexes: list[int] | None = None) -> bool:
    """Authoritative confirmed Fragility feeder with urgent invalidation.

    Stage 1.53 keeps the proven Stage 1.37/1.44 multibox contract but uses the
    owner's native FINISHED cast event as completion proof when the enemy-effect
    cache omits ally-owned Fragility. Shatter clients therefore trust the shared
    active lease and do not require the hex in their own effect cache. The shared
    mutation mutex, dispatch-time packet capture, generation consumption and
    urgent invalidation still protect the conversion path.
    """
    aid = int(agent_id or 0)
    return bool(
        aid > 0
        and int(Fragility_ID or 0) > 0
        and is_fragility_feeder_active(aid)
        and _valid_enemy(aid)
        and not _is_locked(_FRAGILITY_INVALIDATED_LOCK, aid)
    )


def active_fragility_feeder_targets(*, require_spellcast_range: bool = False) -> tuple[int, ...]:
    """Return live feeder-lease targets visible to this client.

    Prefer the authoritative current packet, but fall back to the local enemy
    array so a packet re-anchor between Fragility and Shatter cannot orphan the
    lease.  Optional spellcast-range filtering prevents a carrier reserving a
    target it cannot actually reach.
    """
    candidates: list[int] = []
    try:
        _anchor, members = cluster_anchor_and_members()
        candidates.extend(int(v) for v in members if int(v or 0) > 0)
    except Exception:
        pass
    try:
        candidates.extend(int(v) for v in (AgentArray.GetEnemyArray() or []) if int(v or 0) > 0)
    except Exception:
        pass
    seen: set[int] = set()
    result: list[int] = []
    try:
        player_xy = Player.GetXY()
    except Exception:
        player_xy = None
    for aid in candidates:
        aid = int(aid)
        if aid in seen:
            continue
        seen.add(aid)
        if not is_fragility_shatter_feeder_target(aid):
            continue
        if require_spellcast_range and player_xy is not None:
            try:
                if Utils.Distance(player_xy, Agent.GetXY(aid)) > float(Range.Spellcast.value):
                    continue
            except Exception:
                continue
        result.append(aid)
    return tuple(result)


def _mind_wrack_shatter_allowed(agent_id: int) -> bool:
    aid = int(agent_id or 0)
    if aid <= 0 or not is_mind_wrack_active(aid):
        return False
    # Never shatter the live Aneurysm battery; preserve the zero-Energy burst.
    if _is_locked(_ANEURYSM_REFILLED_LOCK, aid) or _is_locked(_MIND_WRACK_ANCHOR_SETUP_LOCK, aid):
        return False
    try:
        hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
    except Exception:
        hp = 1.0
    if hp <= float(SHATTER_MIND_WRACK_FINISHER_HP):
        return True
    remaining = _hex_lifetime_remaining_ms(aid, int(Mind_Wrack_ID))
    if 0 < remaining <= int(SHATTER_MIND_WRACK_EXPIRY_MS):
        return True
    metrics = esurge_target_metrics(aid)
    density = int(metrics.get("density", 1) or 1)
    projected = float(metrics.get("projected_energy", 0.0) or 0.0)
    drain = float(metrics.get("drain_cap", current_esurge_drain()) or current_esurge_drain())
    # Dense-cluster conversion is allowed only while the target is comfortably
    # above the next zeroing window. Otherwise preserve Wrack for its burst.
    return bool(density >= 3 and projected > max(2.0, 1.35 * drain))


def _fragility_has_immediate_condition_value(agent_id: int) -> bool:
    """Protect a sole Fragility while a real observed condition spread can use it."""
    aid = int(agent_id or 0)
    if aid <= 0 or int(_ready_skill_holders_count(int(Epidemic_ID)) or 0) <= 0:
        return False
    state = observed_condition_state(aid)
    if not state:
        return False
    useful = 0
    for rid in _adjacent_enemy_ids(aid):
        if not _valid_enemy(rid):
            continue
        current = observed_condition_state(rid)
        if any(int(sid) not in current for sid in state):
            useful += 1
    return useful >= 1


def _shatter_salvageable_single_protected_hex(agent_id: int, hexes: list[int]) -> int:
    """Return one protected Mesmer hex that is safe to convert at end-of-life.

    Our API cannot identify which of several Mesmer hexes Shatter Delusions
    would remove.  We therefore salvage Mistrust/Frustration/Ineptitude-family/
    Arcane Conundrum/Fragility only when it is the *only* visible Mesmer hex on
    the target.  This lets spare Shatter copies turn expiring setup into AoE
    damage without gambling a different ally Mesmer's fresh setup.
    """
    aid = int(agent_id or 0)
    visible = [int(v) for v in hexes if int(v or 0) > 0]
    if aid <= 0 or len(visible) != 1:
        return 0
    sid = int(visible[0])
    remaining = int(_hex_lifetime_remaining_ms(aid, sid) or 0)
    if remaining <= 0:
        return 0

    if sid == int(Mistrust_ID):
        # Mistrust only earns its own payoff from the appropriate live spell
        # activation. Preserve that concrete trigger. Otherwise the sole visible
        # copy may be converted at cleanup / near-expiry / dense immediate Shatter
        # value; never assume it is a generic anti-heal hex.
        if is_mistrust_covering_cast(aid):
            return 0
        try:
            hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        except Exception:
            hp = 1.0
        packet_damage, density = expected_shatter_packet_damage(aid)
        if hp <= 0.22:
            return sid
        if remaining <= int(SHATTER_SALVAGE_MISTRUST_MS):
            return sid
        return sid if remaining <= 1800 and int(density) >= 3 and float(packet_damage) >= 150.0 else 0

    if sid == int(Frustration_ID):
        try:
            if Agent.IsCasting(aid):
                return 0
            hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        except Exception:
            hp = 1.0
        return sid if hp <= 0.20 or remaining <= int(SHATTER_SALVAGE_FRUSTRATION_MS) else 0

    if sid in {int(Ineptitude_ID), int(Wandering_Eye_ID), int(Clumsiness_ID)}:
        try:
            if Agent.IsAttacking(aid):
                return 0
        except Exception:
            pass
        return sid if remaining <= int(SHATTER_SALVAGE_ATTACK_HEX_MS) else 0

    if sid == int(Arcane_Conundrum_ID):
        return sid if remaining <= int(SHATTER_SALVAGE_ARCANE_MS) else 0

    if sid == int(Fragility_ID):
        # A published feeder is handled by the authoritative feeder path. For a
        # sole visible non-feeder copy, retain it while an observed condition
        # distribution can immediately exploit the amplifier; otherwise allow
        # low-HP/near-expiry conversion instead of protecting it by identity.
        if _fragility_has_immediate_condition_value(aid):
            return 0
        try:
            hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        except Exception:
            hp = 1.0
        return sid if hp <= 0.22 or remaining <= max(int(SHATTER_SALVAGE_FRAGILITY_MS), 1600) else 0

    if sid == int(Overload_ID):
        # Overload's conditional packet damage is decided at application time;
        # once active, only short degen remains. Preserve most of that duration,
        # then convert the final fraction instead of letting it expire for free.
        try:
            hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        except Exception:
            hp = 1.0
        return sid if hp <= 0.18 or remaining <= int(SHATTER_SALVAGE_OVERLOAD_MS) else 0

    if sid == int(Phantom_Pain_ID):
        # Existing Phantom Pain may be converted as a real cleanup tool when it
        # is the sole removable Mesmer hex.  <20% is never treated as a magic
        # execute threshold: we merely allow the already-paid setup to convert
        # at low HP or near expiry instead of casting a fresh 2 s setup there.
        try:
            hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        except Exception:
            hp = 1.0
        return sid if hp <= 0.22 or remaining <= 1200 else 0

    if sid == int(Web_of_Disruption_ID):
        # Do not sit on a Web waiting for its second interrupt.  If the target is
        # actually using a skill now, or the clean Web is about to expire in a
        # useful packet, converting it is safe and the end interrupt is a bonus.
        try:
            if Agent.IsCasting(aid):
                return sid
        except Exception:
            pass
        _packet_damage, density = expected_shatter_packet_damage(aid)
        return sid if remaining <= 900 and int(density) >= 2 else 0

    # Panic remains hard-protected: even its last moments can still produce a
    # packet-wide interrupt, and it is an elite setup we never trade for Shatter.
    return 0


def shatter_feeder_rejection_summary(excluded_target_ids: tuple[int, ...] = ()) -> dict[str, object]:
    """Explain why live Fragility feeders are not selectable, including lock owner/age."""
    members = tuple(active_fragility_feeder_targets(require_spellcast_range=False))
    excluded = set(int(v) for v in excluded_target_ids if int(v or 0) > 0)
    counts = {
        "active": len(members), "excluded_local": 0, "invalid_enemy": 0,
        "reserved": 0, "mutation_busy": 0, "dispatch_unsafe": 0,
        "out_of_range": 0, "low_hp": 0, "eligible": 0,
    }
    details: list[str] = []
    now = int(_now_ms())
    try:
        player_xy = Player.GetXY()
    except Exception:
        player_xy = None
    for raw in members:
        aid = int(raw)
        generation = int(_fragility_generation_for_target(aid))
        active_rows = _active_skill_target_lock_events((_FRAGILITY_FEEDER_ACTIVE_LOCK,), target_id=aid)
        active_posted = max((int(row.get("posted_at", 0) or 0) for row in active_rows), default=0)
        feeder_age = max(0, now - active_posted) if now > 0 and active_posted > 0 else -1
        prefix = f"t={aid},g={generation},feeder_age={feeder_age}"
        if aid in excluded:
            counts["excluded_local"] += 1; details.append(prefix + ":excluded_local"); continue
        if not _valid_enemy(aid):
            counts["invalid_enemy"] += 1; details.append(prefix + ":invalid_enemy"); continue
        target_rows = _active_skill_target_lock_events((_SHATTER_DELUSIONS_TARGET_LOCK,), target_id=aid)
        if target_rows:
            counts["reserved"] += 1
            row = max(target_rows, key=lambda r: int(r.get("posted_at", 0) or 0))
            local_pending = next((
                r for r in _PENDING_CAST_RECEIPTS
                if int(r.target_id or 0) == aid and int(r.skill_id or 0) == int(Shatter_Delusions_ID or 0)
            ), None)
            pending_state = (
                f"local_shatter(dispatched={bool(int(local_pending.dispatched_tick or 0)>0)},evidence={local_pending.evidence})"
                if local_pending is not None else "remote_or_no_local_receipt"
            )
            details.append(prefix + f":reserved(purpose={row.get('purpose','shatter_target_reservation')},owner={row.get('owner','')},age={max(0,now-int(row.get('posted_at',0) or 0))},left={max(0,int(row.get('expires_at',0) or 0)-now)},pending={pending_state})")
            continue
        mutation_rows = _active_skill_target_lock_events((_HEX_MUTATION_LOCK,), target_id=aid)
        if mutation_rows:
            counts["mutation_busy"] += 1
            row = max(mutation_rows, key=lambda r: int(r.get("posted_at", 0) or 0))
            local_pending = next((
                r for r in _PENDING_CAST_RECEIPTS
                if int(r.target_id or 0) == aid or aid in tuple(int(v) for v in (r.mutation_targets or ()))
            ), None)
            pending_state = (
                f"local_skill={int(local_pending.skill_id or 0)}(dispatched={bool(int(local_pending.dispatched_tick or 0)>0)},evidence={local_pending.evidence})"
                if local_pending is not None else "remote_or_no_local_receipt"
            )
            details.append(prefix + f":mutation_busy(purpose={row.get('purpose','hex_mutation_dispatch_guard')},owner={row.get('owner','')},age={max(0,now-int(row.get('posted_at',0) or 0))},left={max(0,int(row.get('expires_at',0) or 0)-now)},pending={pending_state})")
            continue
        if not _shatter_dispatch_safe(aid):
            counts["dispatch_unsafe"] += 1
            known_hexes = tuple(int(v) for v in _shatter_known_hex_ids(aid) if int(v or 0) > 0)
            lifetime = []
            for sid in known_hexes:
                state = _hex_lifetime_state(aid, int(sid))
                lifetime.append(f"{sid}:{state.get('state','unknown')}:{int(state.get('remaining_ms',0) or 0)}")
            details.append(prefix + ":dispatch_unsafe(hexes=" + ",".join(str(v) for v in known_hexes) + ",lifetime=" + "|".join(lifetime) + ")")
            continue
        if player_xy is not None:
            try:
                if Utils.Distance(player_xy, Agent.GetXY(aid)) > float(Range.Spellcast.value):
                    counts["out_of_range"] += 1; details.append(prefix + ":out_of_range"); continue
            except Exception:
                counts["out_of_range"] += 1; details.append(prefix + ":range_query_error"); continue
        try:
            if float(Agent.GetHealth(aid) or 0.0) <= 0.05:
                counts["low_hp"] += 1; details.append(prefix + ":low_hp"); continue
        except Exception:
            counts["invalid_enemy"] += 1; details.append(prefix + ":health_query_error"); continue
        if not is_fragility_shatter_feeder_target(aid):
            counts["dispatch_unsafe"] += 1; details.append(prefix + ":feeder_no_longer_authoritative"); continue
        counts["eligible"] += 1; details.append(prefix + ":eligible")
    counts["target_ids"] = tuple(int(v) for v in members)
    counts["candidate_lock_details"] = ";".join(details)
    return counts


def reserve_shatter_delusions_target(
    preferred_target_ids: tuple[int, ...] = (), *, feeder_only: bool = False,
    end_of_life_only: bool = False, excluded_target_ids: tuple[int, ...] = (),
) -> int:
    """Reserve different, unambiguous visible hex targets for the Shatter copies.

    Stage 1.44 does not infer hex ordering from an intent lease. Target legality
    is also rechecked at queue execution under the shared mutation mutex.
    """
    _anchor, members = cluster_anchor_and_members()
    if int(Shatter_Delusions_ID or 0) <= 0:
        return 0
    if feeder_only:
        members = active_fragility_feeder_targets(require_spellcast_range=True)
    if not members:
        return 0
    preferred = set(int(v) for v in preferred_target_ids if int(v or 0) > 0)
    excluded = set(int(v) for v in excluded_target_ids if int(v or 0) > 0)
    known_expendable = {int(v) for v in (Mind_Wrack_ID,) if int(v or 0) > 0}
    rows: list[tuple[int, int, int, int, int, float, int, int]] = []
    try:
        player_xy = Player.GetXY()
    except Exception:
        player_xy = None
    for aid in members:
        aid = int(aid)
        if aid in excluded:
            continue
        if (not _valid_enemy(aid) or _is_locked(_SHATTER_DELUSIONS_TARGET_LOCK, aid)
                or _is_locked(_HEX_MUTATION_LOCK, aid) or not _shatter_dispatch_safe(aid)):
            continue
        if player_xy is not None:
            try:
                if Utils.Distance(player_xy, Agent.GetXY(aid)) > float(Range.Spellcast.value):
                    continue
            except Exception:
                continue
        try:
            hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        except Exception:
            hp = 0.0
        if hp <= 0.05:
            continue

        fragility_feeder = bool(is_fragility_shatter_feeder_target(aid))
        if feeder_only and not fragility_feeder:
            continue
        if fragility_feeder:
            hexes = []
        else:
            hexes = _shatter_known_hex_ids(aid)
            if not hexes:
                continue

        density = 1
        try:
            from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
            density = max(1, int(TeamCombatFocus.count_adjacent_enemies(aid, Range.Adjacent.value) or 0))
        except Exception:
            density = 1

        # Feeder is eligible only with one visible Mesmer hex, checked above.
        if fragility_feeder:
            if end_of_life_only:
                continue
            rows.append((1, int(density), 0, 0, 0, float(hp), int(aid == _anchor), -aid))
            continue

        protected = _protected_mesmer_hex_ids(aid)
        if int(Mind_Wrack_ID or 0) in protected and _mind_wrack_shatter_allowed(aid):
            protected.discard(int(Mind_Wrack_ID))

        salvage_sid = int(_shatter_salvageable_single_protected_hex(aid, hexes) or 0)
        if salvage_sid > 0:
            protected.discard(int(salvage_sid))

        if end_of_life_only:
            wrack_eol = bool(
                int(Mind_Wrack_ID or 0) in hexes
                and (
                    (0 < int(_hex_lifetime_remaining_ms(aid, int(Mind_Wrack_ID or 0)) or 0) <= int(SHATTER_MIND_WRACK_EXPIRY_MS))
                    or float(hp) <= float(SHATTER_MIND_WRACK_FINISHER_HP)
                )
                and _mind_wrack_shatter_allowed(aid)
            )
            if int(salvage_sid or 0) <= 0 and not wrack_eol:
                continue

        allowed_expendable = set(known_expendable)
        if salvage_sid > 0:
            allowed_expendable.add(int(salvage_sid))

        unknown = set(int(v) for v in hexes) - allowed_expendable - protected
        if unknown:
            continue
        if any(int(v) in protected for v in hexes):
            continue
        if not any(int(v) in allowed_expendable for v in hexes):
            continue

        own_wrack = 1 if aid in preferred and int(Mind_Wrack_ID or 0) in hexes else 0
        wrack = 1 if int(Mind_Wrack_ID or 0) in hexes else 0
        salvage = 1 if int(salvage_sid) > 0 else 0
        rank_density = int(density)
        if end_of_life_only:
            sole_sid = int(hexes[0]) if len(hexes) == 1 else 0
            remaining = int(_hex_lifetime_remaining_ms(aid, sole_sid) or 0) if sole_sid > 0 else 0
            # End-of-life pass is a deadline scheduler: the shorter remaining
            # lifetime wins before packet density. Low-HP finishers are immediate.
            rank_density = 20000 if float(hp) <= 0.18 else max(1, 10000 - max(0, remaining))
        rows.append((0, int(rank_density), int(own_wrack), int(salvage), int(wrack), float(hp), int(aid == _anchor), -aid))

    rows.sort(reverse=True)
    for _fragility, _density, _own_wrack, _salvage, _wrack, _hp, _anchor_first, neg_aid in rows:
        aid = -int(neg_aid)
        reservation_ms = (
            int(SHATTER_FRAGILITY_FEEDER_RESERVATION_MS)
            if int(_fragility) > 0
            else int(SHATTER_DELUSIONS_RESERVATION_MS)
        )
        if _try_lock(_SHATTER_DELUSIONS_TARGET_LOCK, aid, reservation_ms):
            return int(aid)
    return 0


def shatter_end_of_life_snapshot(agent_id: int) -> dict[str, object]:
    aid = int(agent_id or 0)
    visible = [int(v) for v in _shatter_known_hex_ids(aid) if int(v or 0) > 0]
    sid = int(visible[0]) if len(visible) == 1 else 0
    return {
        "target_id": aid,
        "hex_id": sid,
        "hex_remaining_ms": int(_hex_lifetime_remaining_ms(aid, sid) or 0) if sid > 0 else 0,
        "hex_count": int(len(visible)),
        "projected_energy": round(float(estimate_enemy_projected_energy(aid)), 1),
        "mistrust_trigger_live": bool(is_mistrust_covering_cast(aid)) if sid == int(Mistrust_ID or 0) else False,
        "target_casting": bool(Agent.IsCasting(aid)) if aid > 0 else False,
        "target_attacking": bool(Agent.IsAttacking(aid)) if aid > 0 else False,
    }


def _enemy_score(agent_id: int) -> tuple[float, int, int, float, int]:
    aid = int(agent_id)
    energy = estimate_enemy_projected_energy(aid)
    caster = 0
    priority = 0
    try:
        from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
        scores = EnemyKnowledge.role_scores(aid)
        caster = int(scores.get("caster", 0))
        priority = int(EnemyKnowledge.threat_bonus(aid))
    except Exception:
        pass
    density = 0
    try:
        from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
        density = int(TeamCombatFocus.count_adjacent_enemies(aid, Range.Adjacent.value))
    except Exception:
        pass
    hp = 1.0
    try:
        hp = float(Agent.GetHealth(aid) or 0.0)
    except Exception:
        pass
    # Aneurysm explicitly refills the anchor; prefer consuming that fresh Energy.
    refill_bonus = 100.0 if _is_locked(_ANEURYSM_REFILLED_LOCK, aid) else 0.0
    return (float(energy + refill_bonus), int(density), int(caster + priority), float(hp), -aid)


def _esurge_packet_damage(effective_drain: float, density: int) -> float:
    primary = max(0.0, float(effective_drain)) * ESURGE_DAMAGE_PER_ENERGY
    return primary * (1.0 + max(0, int(density) - 1) * ESURGE_NEARBY_DAMAGE_FACTOR)


def esurge_target_metrics(agent_id: int) -> dict[str, float | int]:
    """Expected packet value for Energy Surge at the local live rank.

    Energy Surge loses Energy only from the selected target, then deals damage
    to that foe and nearby foes for each point actually lost.  Once the target
    has at least the current drain amount, additional target Energy does not
    increase this cast's damage; packet density therefore becomes decisive.
    """
    aid = int(agent_id or 0)
    drain_cap = float(current_esurge_drain())
    reservation = esurge_reservation_state(aid)
    landed_energy = float(reservation.get("committed_estimated_energy", 0.0) or 0.0)
    energy = float(reservation.get("projected_energy", 0.0) or 0.0)
    effective_drain = max(0.0, min(float(drain_cap), float(energy)))
    density = 0
    try:
        from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
        density = int(TeamCombatFocus.count_adjacent_enemies(aid, Range.Adjacent.value) or 0)
    except Exception:
        density = 0
    density = max(1, int(density))
    hp = 1.0
    try:
        hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
    except Exception:
        pass
    # One point of Energy lost deals 7 damage to the primary target and 75% of that damage to each nearby foe.
    expected_total_damage = _esurge_packet_damage(effective_drain, density)
    mind_wrack_zero_bonus = _mind_wrack_zero_bonus(aid, energy, effective_drain)
    expected_total_damage += float(mind_wrack_zero_bonus)
    return {
        "domination": int(current_domination_level()),
        "drain_cap": float(drain_cap),
        "estimated_energy": float(landed_energy),
        "projected_energy": float(energy),
        "pending_esurges": int(reservation.get("pending_count", 0) or 0),
        "effective_drain": float(effective_drain),
        "density": int(density),
        "expected_total_damage": float(expected_total_damage),
        "mind_wrack_active": int(is_mind_wrack_active(aid)),
        "mind_wrack_zero_bonus": float(mind_wrack_zero_bonus),
        "hp": float(hp),
        "aneurysm_anchor": int(_is_locked(_ANEURYSM_REFILLED_LOCK, aid)),
        "aneurysm_neighbor_drain": float(_aneurysm_neighbor_drain_estimate(aid, _latest_aneurysm_refill_tick(aid))),
    }


def esurge_dispatch_metrics(agent_id: int) -> dict[str, float | int]:
    """Value of the cast that has *just* acquired one in-flight reservation.

    ``esurge_target_metrics`` is intentionally future-looking and subtracts every
    pending reservation, which is correct while choosing the next Mesmer target.
    Immediately after this Mesmer reserves its own slot, however, its own cast
    must still be valued against the Energy that existed *before that one slot*.
    This helper adds back exactly one drain-cap worth of projected Energy for the
    dispatching cast while preserving ``projected_energy`` as the post-reservation
    value that following Mesmers should see.
    """
    aid = int(agent_id or 0)
    base = esurge_target_metrics(aid)
    pending = max(0, int(base.get("pending_esurges", 0) or 0))
    drain_cap = float(base.get("drain_cap", 0.0) or 0.0)
    projected_after = float(base.get("projected_energy", 0.0) or 0.0)
    committed = float(base.get("estimated_energy", 0.0) or 0.0)
    available_for_this_cast = float(projected_after)
    if pending > 0:
        available_for_this_cast = min(float(committed), float(projected_after) + float(drain_cap))
    effective = max(0.0, min(float(drain_cap), float(available_for_this_cast)))
    density = max(1, int(base.get("density", 1) or 1))
    base["effective_drain"] = float(effective)
    mind_wrack_zero_bonus = _mind_wrack_zero_bonus(aid, available_for_this_cast, effective)
    base["mind_wrack_zero_bonus"] = float(mind_wrack_zero_bonus)
    base["expected_total_damage"] = _esurge_packet_damage(effective, density) + float(mind_wrack_zero_bonus)
    base["energy_before_own_reservation"] = float(available_for_this_cast)
    base["pending_before_this_cast"] = max(0, int(pending) - 1)
    return base


def _esurge_score(agent_id: int) -> tuple[float, float, int, int, float, int]:
    aid = int(agent_id)
    metrics = esurge_target_metrics(aid)
    caster = 0
    priority = 0
    try:
        from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
        scores = EnemyKnowledge.role_scores(aid)
        caster = int(scores.get("caster", 0))
        priority = int(EnemyKnowledge.threat_bonus(aid))
    except Exception:
        pass
    expected = float(metrics["expected_total_damage"])
    hp = float(metrics["hp"])
    # Do not make a nearly-dead foe the preferred elite anchor when a healthier
    # packet can absorb the full eSurge.  Dedicated <=10% cleanup handles it.
    if hp <= CLEANUP_THRESHOLD:
        expected *= 0.35
    refill_bonus = 900.0 if _is_locked(_ANEURYSM_REFILLED_LOCK, aid) else 0.0
    # Stage 2.24: active Wrack alone is only a mild preference; a target this
    # exact cast can zero gets the strong coordination bonus. The actual Wrack
    # burst is already included in expected_total_damage above, so this is only
    # a scheduling bias to make the paid setup resolve promptly.
    wrack_zero_now = float(metrics.get("mind_wrack_zero_bonus", 0.0) or 0.0) > 0.0
    wrack_bonus = 260.0 if wrack_zero_now else (80.0 if is_mind_wrack_active(aid) else 0.0)
    famine_bonus = 0.0
    try:
        famine_state = famine_active_state()
        projected = float(metrics.get("projected_energy", 0.0) or 0.0)
        effective = float(metrics.get("effective_drain", 0.0) or 0.0)
        if bool(famine_state.get("active", False)) and bool(famine_state.get("covered", False)) and projected > 0.0 and effective + 0.5 >= projected:
            famine_bonus = 260.0 + min(120.0, float(metrics.get("density", 1) or 1) * 20.0)
    except Exception:
        famine_bonus = 0.0
    return (
        float(expected + refill_bonus + wrack_bonus + famine_bonus),
        float(metrics["effective_drain"]),
        int(metrics["density"]),
        int(caster + priority),
        float(hp),
        -aid,
    )


def _shared_focus_damage_anchor(primary_focus: int) -> int:
    """Return the publisher-owned AoE anchor for the current primary focus.

    Stage 2.44 still let every player client re-score all Adjacent candidates
    locally.  The score is deterministic only when every process has the same
    Energy/effect snapshot; the live 2.44 log showed different eSurge centers
    inside the same two-second volley.  Stage 2.45 therefore consumes the
    already shared TeamCombatFocus plan: all accounts either use its AoE anchor
    (when it still splashes the primary) or the primary itself.
    """
    focus = int(primary_focus or 0)
    if focus <= 0:
        return 0
    try:
        from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
        plan = TeamCombatFocus.get_team_combat_plan(
            filter_range=Range.Spellcast.value, minimum_enemies=1,
            consumer_role="esurge_shared_damage_anchor",
        )
        plan_focus = int(getattr(plan, "focus_target", 0) or 0)
        if plan_focus != focus:
            return int(focus)
        aoe = int(getattr(plan, "aoe_anchor", 0) or 0)
        if aoe > 0 and _valid_enemy(aoe):
            try:
                if aoe == focus or float(Utils.Distance(Agent.GetXY(aoe), Agent.GetXY(focus))) <= float(Range.Adjacent.value):
                    return int(aoe)
            except Exception:
                pass
    except Exception:
        pass
    return int(focus)


def _focus_esurge_anchor(primary_focus: int, candidates: list[int]) -> int:
    """Use one shared eSurge center; never locally recenter onto a second foe.

    If the publisher-selected center is not legal on this client, yield this
    eSurge tick instead of choosing a different center.  Losing one cast is
    preferable to reopening the cross-account damage split the user is trying
    to eliminate.  Aneurysm/Famine special lanes remain handled by their
    existing explicit branches in ``reserve_energy_surge_target``.
    """
    focus = int(primary_focus or 0)
    if focus <= 0:
        return 0
    candidate_set = set(int(v) for v in candidates if int(v or 0) > 0)
    canonical = int(_shared_focus_damage_anchor(focus) or 0)
    if canonical <= 0 or canonical not in candidate_set or not _valid_enemy(canonical):
        return 0
    try:
        covers_focus = bool(
            canonical == focus
            or float(Utils.Distance(Agent.GetXY(canonical), Agent.GetXY(focus))) <= float(Range.Adjacent.value)
        )
    except Exception:
        covers_focus = canonical == focus
    if not covers_focus:
        return 0
    return int(canonical)

def _team_damage_focus_pair(*, consumer_role: str = "mesmerway_damage_pair") -> tuple[int, int]:
    """Return the publisher-owned Stage 2.46 primary/secondary damage pair.

    The party leader chooses the cluster and both anchors. Player clients must
    consume that shared pair instead of locally inventing another sub-cluster.
    Cleanup/execute modes intentionally expose only the hard primary.
    """
    try:
        from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
        plan = TeamCombatFocus.get_team_combat_plan(
            filter_range=Range.Spellcast.value, minimum_enemies=1,
            consumer_role=str(consumer_role or "mesmerway_damage_pair"),
        )
        primary = int(getattr(plan, "focus_target", 0) or 0)
        secondary = int(getattr(plan, "secondary_focus_target", 0) or 0)
        if str(getattr(plan, "mode", "none") or "none") in {"cleanup", "low_hp_finish", "st_defense", "krummrinde"}:
            secondary = 0
        if primary <= 0 or not _valid_enemy(primary):
            primary = 0
        if secondary <= 0 or secondary == primary or not _valid_enemy(secondary):
            secondary = 0
        return int(primary), int(secondary)
    except Exception:
        return 0, 0


def _esurge_account_lane() -> tuple[int, int]:
    """Stable lane rank for the live Energy-Surge player accounts.

    Account email is used only as a deterministic shared sort key.  Four eSurge
    accounts therefore resolve to lanes 0..3 identically on every client, which
    gives an exact 2/2 primary-secondary split without racing PostLock slots.
    """
    try:
        email, _gid = _owner_context()
        rows: list[tuple[str, int]] = []
        for acc in _wisdom_same_instance_accounts():
            ids = set(int(v) for v in _wisdom_skill_ids(acc) if int(v or 0) > 0)
            if int(Energy_Surge_ID or 0) not in ids:
                continue
            owner = str(getattr(acc, "AccountEmail", "") or "").strip()
            aid = int(getattr(getattr(acc, "AgentData", None), "AgentID", 0) or 0)
            if owner:
                rows.append((owner.casefold(), aid))
        rows = sorted(set(rows))
        if not rows or not email:
            return 0, max(1, len(rows))
        local = str(email).strip().casefold()
        for idx, row in enumerate(rows):
            if str(row[0]) == local:
                return int(idx), int(len(rows))
        return 0, int(len(rows))
    except Exception:
        return 0, 1


def _assigned_damage_anchor(primary: int, secondary: int) -> tuple[int, int, int]:
    """Return (target, lane, carrier_count) for the two-anchor damage split.

    With four eSurges, lanes 0-1 burn primary and 2-3 burn secondary. With an
    odd count the primary receives the extra carrier. If only one anchor exists,
    every account converges on it.
    """
    p = int(primary or 0)
    q = int(secondary or 0)
    lane, count = _esurge_account_lane()
    count = max(1, int(count))
    if p <= 0:
        return 0, int(lane), int(count)
    if q <= 0:
        return int(p), int(lane), int(count)
    split = max(1, (int(count) + 1) // 2)
    target = int(p if int(lane) < int(split) else q)
    return int(target), int(lane), int(count)


def _aneurysm_anchor_waiting_for_wrack(agent_id: int) -> bool:
    """True only while a fresh anchor has a realistically available Wrack setup.

    This keeps the stronger Stage 1.41 follow-through window from stalling eSurge
    when every Mind Wrack copy is actually recharging or another carrier is busy.
    """
    aid = int(agent_id or 0)
    if aid <= 0 or is_mind_wrack_active(aid):
        return False
    if not _is_locked(_MIND_WRACK_ANCHOR_SETUP_LOCK, aid):
        return False
    return bool(_ready_skill_holders_count(int(Mind_Wrack_ID)) > 0)


def _famine_esurge_setup_focus(candidate_scope: list[int]) -> tuple[int, bool]:
    """Return one shared pre-Aneurysm eSurge battery and whether to hold it.

    The Stage 2.15 setup is intentionally narrow: Famine must cover the team,
    at least one Aneurysm holder must currently be ready, and the target must
    sit in a real adjacent packet.  Once a shared focus exists, followers keep
    draining that same battery until projected Energy is exhausted; at that
    point ``(0, True)`` tells eSurge to stop spreading and leave the empty
    battery for Aneurysm.
    """
    scope = [int(v) for v in candidate_scope if int(v or 0) > 0]
    if not scope:
        return 0, False
    try:
        famine = famine_active_state()
        famine_covered = bool(famine.get("active", False) and famine.get("covered", False))
    except Exception:
        famine_covered = False
    if not famine_covered or int(_ready_skill_holders_count(int(Aneurysm_ID)) or 0) <= 0:
        return 0, False

    # Honor the newest live shared focus first. If it has already been drained
    # below the normal eSurge value floor, hold fire instead of spreading onto a
    # second enemy: Aneurysm now owns the next energy-reset step.
    scope_set = set(scope)
    for aid in active_famine_esurge_focus_ids():
        aid = int(aid)
        if aid not in scope_set:
            continue
        metrics = esurge_target_metrics(aid)
        if int(metrics.get("density", 1) or 1) < int(FAMINE_ESURGE_FOCUS_MIN_DENSITY):
            continue
        if float(metrics.get("expected_total_damage", 0.0) or 0.0) < float(ESURGE_MIN_EXPECTED_TOTAL_DAMAGE):
            return 0, True
        if float(metrics.get("projected_energy", 0.0) or 0.0) <= 0.5:
            return 0, True
        return aid, True

    # No focus yet: choose a deterministic central, high-energy battery. More
    # adjacent foes comes first because the follow-up Aneurysm can drain every
    # one of them; max/current Energy then maximize the refill/drain amplitude.
    rows: list[tuple[tuple[int, float, int, float, tuple[float, float, int, int, float, int]], int]] = []
    for aid in scope:
        try:
            es = esurge_target_metrics(aid)
            if int(es.get("density", 1) or 1) < int(FAMINE_ESURGE_FOCUS_MIN_DENSITY):
                continue
            if float(es.get("expected_total_damage", 0.0) or 0.0) < float(ESURGE_MIN_EXPECTED_TOTAL_DAMAGE):
                continue
            an = aneurysm_target_metrics(aid)
            affected = len(tuple(an.get("affected_neighbors", ()) or ()))
            if affected <= 0:
                continue
            score = (
                int(affected),
                float(an.get("max_energy", 0.0) or 0.0),
                int(es.get("density", 1) or 1),
                float(es.get("projected_energy", 0.0) or 0.0),
                _esurge_score(aid),
            )
            rows.append((score, int(aid)))
        except Exception:
            continue
    if not rows:
        return 0, False
    rows.sort(reverse=True)
    return int(rows[0][1]), True


def reserve_energy_surge_target() -> int:
    """Prefer reachable refill anchors, even outside the current packet.

    Otherwise score actual projected energy; a partial neighbor drain alone
    must not exclude an otherwise useful primary target.
    """
    _anchor, members = cluster_anchor_and_members()
    primary_focus = int(_team_primary_focus_target(consumer_role="esurge_primary_focus") or 0)
    healthy_members: list[int] = []
    for aid in members:
        try:
            if float(Agent.GetHealth(int(aid)) or 0.0) > float(CLEANUP_THRESHOLD):
                healthy_members.append(int(aid))
        except Exception:
            healthy_members.append(int(aid))

    # Stage 1.31: consume the newest live Aneurysm battery first.  We do not
    # merely add a score bonus: if the anchor can still produce a legal eSurge,
    # other packet members are intentionally not eligible as primary targets.
    anchor_candidates: list[int] = []
    deferred_wrack_setup: set[int] = set()
    for aid in active_aneurysm_anchor_ids():
        if int(aid) not in set(int(v) for v in members) or not _valid_enemy(int(aid)):
            continue
        if (float(Agent.GetHealth(int(aid))) <= float(CLEANUP_THRESHOLD)
                or math.dist(Player.GetXY(), Agent.GetXY(int(aid))) > float(Range.Spellcast.value)):
            continue
        if _aneurysm_anchor_waiting_for_wrack(int(aid)):
            deferred_wrack_setup.add(int(aid))
            continue
        metrics = esurge_target_metrics(int(aid))
        if float(metrics.get("expected_total_damage", 0.0) or 0.0) < float(ESURGE_MIN_EXPECTED_TOTAL_DAMAGE):
            continue
        anchor_candidates.append(int(aid))

    # Stage 1.64: while a Wrack->Aneurysm transaction is live, neither the
    # setup neighbours nor the not-yet-refilled anchor may be consumed by an
    # opportunistic eSurge.  Stage 1.63 logs showed this race invalidating the
    # projected-energy window before Mind Wrack could native-finish.
    protected_prewrack_targets = set(int(v) for v in active_aneurysm_prewrack_target_ids())
    protected_prewrack_anchors = set(int(v) for v in active_aneurysm_precast_anchor_ids())
    protected_prewrack = protected_prewrack_targets | protected_prewrack_anchors

    candidate_scope = list(anchor_candidates) if anchor_candidates else [
        int(aid) for aid in healthy_members
        if int(aid) not in deferred_wrack_setup
        and int(aid) not in protected_prewrack
    ]
    if not candidate_scope:
        return 0

    # Stage 2.44/2.45 compatibility markers retained for historical regression
    # contracts; Stage 2.46 deliberately supersedes the one-anchor behavior:
    # _focus_esurge_anchor(primary_focus, candidate_scope)
    # candidate_scope = [int(focus_esurge_anchor)]
    # stage2_45_shared_primary_anchor_soft_overkill_dynamic_energy
    # Stage 2.46: normal damage is one shared cluster with at most two central
    # support-first anchors.  Four eSurge accounts split deterministically 2/2
    # across those anchors.  No client locally re-scores a third target.
    focus_esurge_anchor = 0
    secondary_focus = 0
    damage_lane = 0
    damage_carriers = 1
    if not anchor_candidates:
        primary_focus, secondary_focus = _team_damage_focus_pair(consumer_role="esurge_two_anchor_pair")
        assigned, damage_lane, damage_carriers = _assigned_damage_anchor(primary_focus, secondary_focus)
        scope_set = set(int(v) for v in candidate_scope)
        # Stage 2.49: a temporarily empty/depleted/locked assigned anchor
        # should not idle an entire eSurge account if the *other already-shared*
        # damage anchor remains legal. This is NOT permission to select a third
        # target or start a new combat packet. Projected energy, in-flight slots
        # and Mind Wrack commitments still determine whether a cast is useful.
        def can_burn_anchor(aid: int) -> bool:
            if int(aid) <= 0 or int(aid) not in scope_set:
                return False
            if _is_locked(_MIND_WRACK_CAST_LOCK, int(aid)) and not is_mind_wrack_active(int(aid)):
                return False
            if int(_live_lock_count(_ESURGE_SLOT_LOCKS, int(aid))) >= int(ESURGE_PRIMARY_FOCUS_MAX_RESERVATIONS):
                return False
            return bool(
                float(esurge_target_metrics(int(aid)).get("expected_total_damage", 0.0) or 0.0)
                >= float(ESURGE_MIN_EXPECTED_TOTAL_DAMAGE)
            )

        if can_burn_anchor(int(assigned)):
            focus_esurge_anchor = int(assigned)
            candidate_scope = [int(assigned)]
            alternate = int(secondary_focus if int(assigned) == int(primary_focus) else primary_focus)
            if (int(primary_focus) > 0 and alternate != int(assigned)
                    and can_burn_anchor(alternate)):
                try:
                    # Only two PUBLISHED centers, both still cover the team's
                    # authoritative primary. No distant third damage packet.
                    primary_xy = Agent.GetXY(int(primary_focus))
                    both_cover_primary = all(
                        Utils.Distance(Agent.GetXY(int(aid)), primary_xy)
                        <= float(Range.Adjacent.value)
                        for aid in (int(assigned), int(alternate))
                    )
                    current = esurge_target_metrics(int(assigned))
                    candidate = esurge_target_metrics(int(alternate))
                    cur_density = int(current.get('density', 0) or 0)
                    alt_density = int(candidate.get('density', 0) or 0)
                    cur_damage = float(current.get('expected_total_damage', 0) or 0)
                    alt_damage = float(candidate.get('expected_total_damage', 0) or 0)
                    primary_hp = float(Agent.GetHealth(int(primary_focus)) or 0)
                    if (both_cover_primary and primary_hp > 0.30
                            and alt_density >= cur_density + 2
                            and alt_damage >= cur_damage * 1.45):
                        focus_esurge_anchor = int(alternate)
                        candidate_scope = [int(alternate)]
                        try:
                            from Py4GWCoreLib.Builds.Skills import CombatDebug
                            CombatDebug.log_event(
                                'MESMERWAY_ESURGE_SAME_FOCUS_AOE_UPGRADE',
                                assigned_target=int(assigned), selected_target=int(alternate),
                                primary_target=int(primary_focus),
                                assigned_density=int(cur_density), selected_density=int(alt_density),
                                assigned_damage=round(cur_damage, 1), selected_damage=round(alt_damage, 1),
                                policy='stage2_56_published_pair_preserves_primary_no_third_lane',
                            )
                        except Exception:
                            pass
                except Exception:
                    pass
        else:
            fallback = int(secondary_focus if int(assigned) == int(primary_focus) else primary_focus)
            if can_burn_anchor(fallback) and fallback != int(assigned):
                focus_esurge_anchor = int(fallback)
                candidate_scope = [int(fallback)]
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_event(
                        "MESMERWAY_ESURGE_SHARED_ANCHOR_FAILOVER",
                        assigned_target=int(assigned), fallback_target=int(fallback),
                        primary_focus=int(primary_focus), secondary_focus=int(secondary_focus),
                        damage_lane_index=int(damage_lane),
                        reason="assigned_unavailable_or_low_projected_damage",
                        policy="stage2_49_no_third_lane_existing_shared_pair_only",
                    )
                except Exception:
                    pass
            elif primary_focus > 0 or secondary_focus > 0:
                # No legal alternate shared anchor: deliberately yield rather
                # than opening the old free-for-all target scatter.
                return 0

    # Stage 2.15: before Aneurysm, Famine-covered packets may intentionally
    # concentrate the eSurge volley onto one shared battery. Existing post-
    # Aneurysm anchors still have first refusal through ``anchor_candidates``.
    famine_setup_target = 0
    famine_setup_hold = False
    if not anchor_candidates:
        famine_setup_target, famine_setup_hold = _famine_esurge_setup_focus(candidate_scope)
        if famine_setup_hold and int(famine_setup_target) <= 0:
            return 0
        if int(famine_setup_target) > 0:
            candidate_scope = [int(famine_setup_target)]

    rows: list[tuple[tuple[float, float, int, int, float, int], int, int, int, bool]] = []
    for aid in candidate_scope:
        # If a Wrack carrier has already reserved this target, give the hex a
        # brief chance to land before consuming the battery. Fail-open is the
        # natural lock expiry, so a dead/out-of-range carrier cannot stall DPS.
        if _is_locked(_MIND_WRACK_CAST_LOCK, int(aid)) and not is_mind_wrack_active(int(aid)):
            continue
        # Normal targets retain the distribution rule (max two simultaneous
        # reservations).  A live Aneurysm battery is the deliberate exception:
        # it may accept all four eSurges, but only sequentially while the shared
        # projected-Energy model says the *next* packet is still worth casting.
        is_aneurysm_anchor = bool(int(aid) in anchor_candidates)
        is_famine_setup_focus = bool(int(famine_setup_target) > 0 and int(aid) == int(famine_setup_target))
        is_primary_focus_lane = bool(int(focus_esurge_anchor or 0) > 0 and int(aid) == int(focus_esurge_anchor))
        fan_in_limit = (
            int(ANEURYSM_ANCHOR_MAX_ESURGE_RESERVATIONS)
            if is_aneurysm_anchor
            else int(FAMINE_ESURGE_FOCUS_MAX_RESERVATIONS)
            if is_famine_setup_focus
            else int(ESURGE_PRIMARY_FOCUS_MAX_RESERVATIONS)
            if is_primary_focus_lane
            else int(ESURGE_NORMAL_TARGET_MAX_RESERVATIONS)
        )
        claim_count = int(_live_lock_count(_ESURGE_SLOT_LOCKS, aid))
        if claim_count >= int(fan_in_limit):
            continue
        metrics = esurge_target_metrics(int(aid))
        if float(metrics.get("expected_total_damage", 0.0) or 0.0) < float(ESURGE_MIN_EXPECTED_TOTAL_DAMAGE):
            continue
        rows.append((_esurge_score(aid), int(aid), int(claim_count), int(fan_in_limit), bool(is_famine_setup_focus)))
    if not rows:
        return 0

    rows.sort(reverse=True)
    for _score, aid, _claims, fan_in_limit, is_famine_setup_focus in rows:
        for lock_id in _ESURGE_SLOT_LOCKS:
            if _live_lock_count((int(lock_id),), aid) > 0:
                continue
            if _try_lock(lock_id, aid, ESURGE_TARGET_RESERVATION_MS):
                if bool(is_famine_setup_focus):
                    # Marker only: the first Mesmer posts it, followers observe
                    # it. Failure means another account already owns the same
                    # exclusive marker and is therefore also evidence of focus.
                    _try_lock(_FAMINE_ESURGE_FOCUS_LOCK, int(aid), FAMINE_ESURGE_FOCUS_MS)
                # Re-read through the uncached path so same-frame followers see
                # this reservation in projected Energy immediately.
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    state = esurge_reservation_state(int(aid))
                    density = int(esurge_target_metrics(int(aid)).get("density", 0) or 0)
                    CombatDebug.log_event(
                        "MESMERWAY_ESURGE_RESERVATION",
                        target_id=int(aid),
                        slot_lock_id=int(lock_id),
                        slot_count=int(state.get("slot_count", 0) or 0),
                        hit_count=int(state.get("hit_count", 0) or 0),
                        pending_esurges=int(state.get("pending_count", 0) or 0),
                        projected_energy=f"{float(state.get('projected_energy', 0.0) or 0.0):.1f}",
                        projected_aoe_targets=int(density),
                        single_target_packet=bool(density <= 1),
                        aneurysm_anchor=bool(int(aid) in anchor_candidates),
                        famine_setup_focus=bool(is_famine_setup_focus),
                        fan_in_limit=int(fan_in_limit),
                        fan_in_after_reservation=int(state.get("slot_count", 0) or 0),
                        team_primary_focus=int(primary_focus),
                        team_secondary_focus=int(secondary_focus or 0),
                        focus_damage_lane=bool(is_primary_focus_lane),
                        damage_lane_index=int(damage_lane),
                        damage_carrier_count=int(damage_carriers),
                        shared_damage_anchor=int(focus_esurge_anchor or 0),
                        policy="stage2_46_two_anchor_support_first_2x2_damage",
                    )
                except Exception:
                    pass
                return int(aid)
    return 0

def release_energy_surge_reservation(target_id: int, *, reason: str = "not_fired") -> int:
    """Release this account's exact eSurge in-flight slot(s) for one target.

    SharedMemory currently exposes a broad owner/kind/target clear helper but not
    one filtered by whiteboard key. Clearing the broad target would also remove
    unrelated Mesmerway setup/interrupt locks, so this helper narrowly resets
    only our four eSurge slot keys. It is used only when dispatch was rejected.
    """
    aid = int(target_id or 0)
    if aid <= 0:
        return 0
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        if not email:
            return 0
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccounts()
        count = 0
        for intent in all_accounts.Intents:
            if not getattr(intent, "Active", False):
                continue
            if str(getattr(intent, "OwnerEmail", "") or "") != str(email):
                continue
            if int(getattr(intent, "KindID", 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET):
                continue
            if int(getattr(intent, "SkillID", 0) or 0) not in _ESURGE_SLOT_LOCKS:
                continue
            if int(getattr(intent, "TargetAgentID", 0) or 0) != int(aid):
                continue
            if int(getattr(intent, "IsolationGroupID", 0) or 0) != int(gid):
                continue
            intent.reset()
            count += 1
        if count > 0:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_ESURGE_RESERVATION_RELEASE",
                    target_id=int(aid), released=int(count), reason=str(reason),
                    policy="stage1_26_exact_failed_dispatch_release",
                )
            except Exception:
                pass
        return int(count)
    except Exception:
        return 0


def register_energy_surge_cast(target_id: int) -> None:
    aid = int(target_id or 0)
    if aid <= 0:
        return
    for lock_id in _ESURGE_HIT_LOCKS:
        if _is_locked(lock_id, aid):
            continue
        if _try_lock(lock_id, aid, ESURGE_HIT_EVIDENCE_MS):
            return


def _effect_ids(agent_id: int) -> list[int]:
    effects = list(GLOBAL_CACHE.Effects.GetBuffs(int(agent_id)) or [])
    effects += list(GLOBAL_CACHE.Effects.GetEffects(int(agent_id)) or [])
    return [int(getattr(effect, "skill_id", 0) or 0) for effect in effects]


def is_mistrust_protected(agent_id: int) -> bool:
    """Protect pending/active Mistrust, but release immediately when consumed."""
    aid = int(agent_id or 0)
    if aid <= 0:
        return False
    if _is_locked(_MISTRUST_CAST_LOCK, aid):
        return True
    try:
        # Successful effect visibility is authoritative. An empty list means the
        # hex is gone, so do NOT keep blocking interrupts for the old 6s window.
        return int(Mistrust_ID) in _effect_ids(aid)
    except Exception:
        # Only use the duration fallback when effect visibility itself failed.
        return _is_locked(_MISTRUST_ACTIVE_FALLBACK_LOCK, aid)


def _mistrust_effect_visible(agent_id: int) -> bool:
    """True only when the actual Mistrust effect is visible on the foe.

    The pending target lease is intentionally excluded: a dangerous cast must not
    be allowed through merely because one of our Mesmers has *started* Mistrust.
    """
    aid = int(agent_id or 0)
    if aid <= 0:
        return False
    try:
        return int(Mistrust_ID) in _effect_ids(aid)
    except Exception:
        return False


def _target_is_our_ally(target_id: int) -> bool:
    tid = int(target_id or 0)
    if tid <= 0:
        return False
    try:
        _allegiance_id, allegiance_name = Agent.GetAllegiance(tid)
        if str(allegiance_name) == "Ally":
            return True
        # Spirits/minions can carry a neutral allegiance bucket. Treat them as
        # ours only when their owner is positively known as an ally.
        if str(allegiance_name) in ("Spirit/Pet", "Minion"):
            owner_id = int(Agent.GetOwnerID(tid) or 0)
            if owner_id > 0:
                _owner_alg, owner_name = Agent.GetAllegiance(owner_id)
                return str(owner_name) == "Ally"
    except Exception:
        pass
    return False


def is_mistrust_covering_cast(agent_id: int, enemy_skill_id: int | None = None) -> bool:
    """Return True only for the *current activation* Mistrust can actually punish.

    Mistrust is not a blanket no-interrupt state. We require three positive facts:
    the hex effect is visible, the current enemy action is a spell, and the native
    cast event identifies one of our allies as its target. Unknown target data is handled conservatively by skill value: known heals/
    protection/danger remain interruptible; low-value ambiguous spells preserve the
    Mistrust trigger.
    """
    aid = int(agent_id or 0)
    sid = int(enemy_skill_id or _casting_skill_id(aid) or 0)
    if aid <= 0 or sid <= 0 or not _mistrust_effect_visible(aid):
        return False
    try:
        if not GLOBAL_CACHE.Skill.Flags.IsSpell(int(sid)):
            return False
    except Exception:
        return False
    target_id = 0
    try:
        from Py4GWCoreLib.Builds.Skills import ReforgedSupport
        target_id = int(ReforgedSupport.get_cast_target_id(aid, int(sid)) or 0)
    except Exception:
        target_id = 0
    if target_id > 0:
        covered = bool(_target_is_our_ally(target_id))
        decision_basis = "native_target_ally" if covered else "native_target_not_ally"
    else:
        # Unknown native cast targets are the only ambiguous case. Preserve a
        # low-value Mistrust trigger, but never let a known heal/protection/
        # dangerous action through merely because the event bridge missed target.
        priority = int(_interrupt_priority(aid, sid))
        covered = bool(priority < 80)
        decision_basis = "unknown_target_preserve_low_value" if covered else "unknown_target_safety_interrupt"
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_keyed_state_event(
            "MESMERWAY_MISTRUST_CAST_GUARD",
            (int(aid), int(sid), int(target_id)),
            signature_fields=("caster_id", "enemy_skill_id", "cast_target_id", "covered", "decision_basis"),
            min_interval_ms=300, heartbeat_ms=1800,
            caster_id=int(aid), enemy_skill_id=int(sid), cast_target_id=int(target_id),
            covered=bool(covered), decision_basis=str(decision_basis),
            policy="stage1_25_cast_specific_unknown_target_safety",
        )
    except Exception:
        pass
    return bool(covered)


def is_frustration_focused(agent_id: int) -> bool:
    """True while Frustration is pending or actually active on this enemy.

    The short pending lease bridges effect-propagation latency; once visible, the
    real effect is authoritative. This is deliberately a *soft* focus: emergency
    casts elsewhere may still override it.
    """
    aid = int(agent_id or 0)
    if aid <= 0:
        return False
    if _is_locked(_FRUSTRATION_TARGET_LOCK, aid):
        return True
    try:
        return int(Frustration_ID) in _effect_ids(aid)
    except Exception:
        return False


def _is_overload_setup_protected(agent_id: int, enemy_skill_id: int = 0) -> bool:
    """Short non-emergency protection so Overload can land before our interrupt.

    This is intentionally ignored for high-danger activations. It is not an
    Overload-hex duration model; it only covers the dispatch window of the
    instant conditional-damage cast.
    """
    aid = int(agent_id or 0)
    if aid <= 0 or not _is_locked(_OVERLOAD_SETUP_LOCK, aid):
        return False
    sid = int(enemy_skill_id or _casting_skill_id(aid) or 0)
    if sid > 0 and _interrupt_priority(aid, sid) >= int(FRUSTRATION_DANGER_OVERRIDE_SCORE):
        return False
    return True


def _ready_cry_holders_count() -> int:
    """Best-effort count of immediately ready Cry holders in shared memory."""
    ready = 0
    try:
        for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
            skillbar = getattr(getattr(account, "AgentData", None), "Skillbar", None)
            if skillbar is None or int(getattr(skillbar, "CastingSkillID", 0) or 0) > 0:
                continue
            for skill in getattr(skillbar, "Skills", ()) or ():
                if int(getattr(skill, "Id", 0) or 0) != int(Cry_of_Frustration_ID):
                    continue
                if float(getattr(skill, "Recharge", 0.0) or 0.0) <= 0.0:
                    ready += 1
                break
    except Exception:
        pass
    return max(1, int(ready))


def reserve_mistrust_target() -> int:
    _anchor, members = cluster_anchor_and_members()
    candidates: list[tuple[int, int, int, int]] = []
    for aid in members:
        if is_mistrust_protected(aid) or _is_locked(_MISTRUST_CAST_LOCK, aid):
            continue
        if is_fragility_feeder_window(int(aid)) or is_optional_shatter_pipeline_pending(int(aid)):
            continue
        caster = 0
        priority = 0
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            caster = int(EnemyKnowledge.role_scores(aid).get("caster", 0))
            priority = int(EnemyKnowledge.mistrust_priority(aid))
        except Exception:
            pass
        try:
            if Agent.IsCaster(aid):
                caster += 80
        except Exception:
            pass
        if caster <= 0:
            continue
        casting_skill = int(_casting_skill_id(aid) > 0)
        candidates.append((casting_skill, priority, caster, int(aid)))
    candidates.sort(reverse=True)
    for _casting, _priority, _caster, aid in candidates:
        if _try_lock(_MISTRUST_CAST_LOCK, aid, MISTRUST_PENDING_MS):
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_keyed_state_event(
                    "MESMERWAY_MISTRUST_TARGET_RESERVED", f"mistrust:{int(aid)}",
                    signature_fields=("target_id", "live_enemy_cast"), min_interval_ms=700,
                    target_id=int(aid), live_enemy_cast=bool(_casting),
                    pending_ms=int(MISTRUST_PENDING_MS),
                    policy="stage2_55_shared_lease_real_effect_required_for_long_coverage",
                )
            except Exception:
                pass
            return int(aid)
    return 0


def register_mistrust_cast(target_id: int) -> None:
    _try_lock(_MISTRUST_ACTIVE_FALLBACK_LOCK, int(target_id), MISTRUST_ACTIVE_FALLBACK_MS)


def _casting_skill_id(agent_id: int) -> int:
    aid = int(agent_id or 0)
    try:
        if not Agent.IsCasting(aid):
            return 0
        return max(0, int(Agent.GetCastingSkillID(aid) or 0))
    except Exception:
        return 0


def _is_spell_or_chant(skill_id: int) -> bool:
    try:
        flags = GLOBAL_CACHE.Skill.Flags
        return bool(flags.IsSpell(int(skill_id)) or flags.IsChant(int(skill_id)))
    except Exception:
        return False


def _interrupt_feasible(target_id: int, our_skill_id: int) -> bool:
    """Use the native timing model with extra live multibox safety margin.

    The first Mesmerway field log showed several legacy Cry attempts being
    recorded only after the enemy cast had completed.  A 220ms reaction margin
    is deliberately more conservative than the old 90ms path; missing a late
    low-value interrupt is preferable to consuming Cry/Power on a dead cast.
    """
    try:
        from Py4GWCoreLib.HeroAI import interrupt as reforged_interrupt
        try:
            fast_casting = int(reforged_interrupt._get_player_fast_casting_level() or 0)
        except Exception:
            fast_casting = 0
        try:
            ping_ms = int(reforged_interrupt._PING_HANDLER.GetCurrentPing() or 100)
        except Exception:
            ping_ms = 100
        if ping_ms <= 0:
            ping_ms = 100
        return bool(reforged_interrupt.is_interrupt_feasible(
            int(target_id), int(our_skill_id), int(fast_casting), int(ping_ms),
            reaction_margin_ms=220, debug=False,
        ))
    except Exception:
        try:
            from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import interrupt_is_feasible
            return bool(interrupt_is_feasible(int(target_id), int(our_skill_id)))
        except Exception:
            return True


def _interrupt_priority(agent_id: int, enemy_skill_id: int) -> int:
    try:
        from Py4GWCoreLib.Builds.Skills import DangerousSkillPriorities as DSP
        return int(DSP.contextual_score(int(enemy_skill_id), adjacent_enemies=1))
    except Exception:
        return 0


def _interrupt_remaining_ms(agent_id: int, enemy_skill_id: int) -> int | None:
    try:
        from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import get_enemy_cast_remaining_ms
        remaining = get_enemy_cast_remaining_ms(int(agent_id), int(enemy_skill_id))
        return None if remaining is None else max(0, int(remaining))
    except Exception:
        return None


def _interrupt_min_remaining_ms(
    our_skill_id: int, *, emergency: bool = False, enemy_skill_id: int = 0
) -> int:
    sid = int(our_skill_id or 0)
    enemy_sid = int(enemy_skill_id or 0)
    if sid == int(Complicate_ID):
        return int(
            INTERRUPT_EMERGENCY_MIN_REMAINING_COMPLICATE_MS
            if emergency else INTERRUPT_MIN_REMAINING_COMPLICATE_MS
        )
    if sid == int(Cry_of_Frustration_ID):
        if enemy_sid == int(Splinter_Weapon_ID or 0):
            return int(
                SPLINTER_WEAPON_INTERRUPT_DISPATCH_MIN_REMAINING_CRY_MS
                if emergency else SPLINTER_WEAPON_INTERRUPT_MIN_REMAINING_CRY_MS
            )
        return int(INTERRUPT_MIN_REMAINING_CRY_MS)
    if sid == int(Power_Spike_ID):
        return int(
            INTERRUPT_EMERGENCY_MIN_REMAINING_POWER_SPIKE_MS
            if emergency else INTERRUPT_MIN_REMAINING_POWER_SPIKE_MS
        )
    if sid == int(Psychic_Instability_ID):
        # PI is intentionally spam-control in Stage 1.72. Only require a small
        # dispatch window so short attack/skill activations can still be used.
        return 120
    return 0


def _log_interrupt_late_skip(agent_id: int, enemy_skill_id: int, our_skill_id: int, remaining_ms: int, minimum_ms: int) -> None:
    """Diagnostic gate with cast-opportunity identity and measured queue delay."""
    global _LAST_INTERRUPT_LATE_SKIP_LOG_MS
    now = int(_now_ms() or 0)
    aid = int(agent_id or 0)
    enemy_sid = int(enemy_skill_id or 0)
    our_sid = int(our_skill_id or 0)
    key = (aid, enemy_sid, our_sid)
    # Approximate the same enemy activation by its projected end tick. Repeated
    # controller evaluations of one cast therefore no longer look like separate
    # missed opportunities in telemetry.
    projected_end_ms = int(now + max(0, int(remaining_ms))) if now > 0 else int(remaining_ms)
    bucket = int(projected_end_ms // 150)
    previous_bucket, previous_count = _INTERRUPT_LATE_EVAL_STATE.get(key, (-1, 0))
    repeat = abs(int(previous_bucket) - int(bucket)) <= 1
    eval_count = int(previous_count + 1) if repeat else 1
    _INTERRUPT_LATE_EVAL_STATE[key] = (int(bucket), int(eval_count))
    if now > 0 and now < int(_LATE_INTERRUPT_SKIP_LOG_UNTIL.get(key, 0) or 0):
        return
    if now > 0:
        _LATE_INTERRUPT_SKIP_LOG_UNTIL[key] = int(now + 2500)
        # Late-skip is a normal guard outcome, not an error. Keep enough samples
        # to reconstruct queue-delay behaviour without logging every enemy cast.
        if now - int(_LAST_INTERRUPT_LATE_SKIP_LOG_MS or 0) < 3000:
            return
        _LAST_INTERRUPT_LATE_SKIP_LOG_MS = int(now)
    p50, p90, maximum = _interrupt_dispatch_delay_stats()
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_INTERRUPT_LATE_SKIP",
            caster_id=aid, enemy_skill_id=enemy_sid, our_skill_id=our_sid,
            estimated_remaining_ms=int(remaining_ms), required_remaining_ms=int(minimum_ms),
            opportunity_end_bucket=int(bucket), repeated_evaluation=bool(repeat),
            evaluation_count=int(eval_count),
            measured_queue_delay_p50_ms=int(p50), measured_queue_delay_p90_ms=int(p90),
            measured_queue_delay_max_ms=int(maximum), measured_samples=len(_INTERRUPT_DISPATCH_DELAYS_MS),
            policy="stage1_55_threshold_unchanged_measure_queue_delay_and_dedupe_opportunity",
        )
    except Exception:
        pass


def _claim_exact_interrupt(target_id: int, enemy_skill_id: int, our_skill_id: int) -> bool:
    """Claim one exact enemy cast across all Mesmerway accounts/controllers."""
    aid = int(target_id or 0)
    enemy_sid = int(enemy_skill_id or 0)
    our_sid = int(our_skill_id or 0)
    if aid <= 0 or enemy_sid <= 0 or our_sid <= 0:
        return False
    # Stage 1.72: a PI carrier that has reserved the packet gets first refusal
    # before Cry/Power/Web/Complicate. This is a control reservation only; the
    # exact-cast claim below still arbitrates the concrete enemy activation.
    if int(our_sid) != int(Psychic_Instability_ID):
        try:
            from Py4GWCoreLib.Builds.Skills import PIMesmerCoordination
            if PIMesmerCoordination.pi_packet_reserved((int(aid),)):
                try:
                    from Py4GWCoreLib.Builds.Skills import CombatDebug
                    CombatDebug.log_keyed_state_event(
                        "MESMERWAY_INTERRUPT_DEFER_PI_FIRST_REFUSAL",
                        (int(aid), int(enemy_sid)),
                        signature_fields=("caster_id", "enemy_skill_id", "our_skill_id"),
                        min_interval_ms=500, heartbeat_ms=2000,
                        caster_id=int(aid), enemy_skill_id=int(enemy_sid), our_skill_id=int(our_sid),
                        policy="stage1_72_foreign_pi_packet_first_refusal",
                    )
                except Exception:
                    pass
                return False
        except Exception:
            pass
    try:
        from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import (
            post_interrupt_claim, release_unviable_interrupt_owner_claims,
        )
        if not bool(post_interrupt_claim(aid, enemy_sid, our_sid)):
            # One bounded backup attempt: only if shared state proves the current
            # foreign owner cannot deliver a direct interrupt anymore.
            if not bool(release_unviable_interrupt_owner_claims(
                aid, enemy_sid, skill_target_lock_id=int(_INTERRUPT_TARGET_LOCK)
            )):
                return False
            if not bool(post_interrupt_claim(aid, enemy_sid, our_sid)):
                return False
        _EXACT_INTERRUPT_RESERVED_CASTS[(aid, our_sid)] = enemy_sid
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_INTERRUPT_CLAIM",
                caster_id=int(aid),
                enemy_skill_id=int(enemy_sid),
                our_skill_id=int(our_sid),
                estimated_remaining_ms=int(_interrupt_remaining_ms(aid, enemy_sid) or 0),
                policy="exact_cast_cross_account_claim_early_window",
            )
        except Exception:
            pass
        return True
    except Exception:
        # Preserve previous fail-open behavior if the central claim layer is unavailable.
        _EXACT_INTERRUPT_RESERVED_CASTS[(aid, our_sid)] = enemy_sid
        return True


def _release_exact_interrupt(target_id: int, our_skill_id: int, *, reason: str) -> None:
    aid = int(target_id or 0)
    our_sid = int(our_skill_id or 0)
    _INTERRUPT_DISPATCH_TOKENS.pop((aid, our_sid), None)
    _PI_PROTECTED_OVERRIDE_CASTS.pop((aid, our_sid), None)
    enemy_sid = int(_EXACT_INTERRUPT_RESERVED_CASTS.pop((aid, our_sid), 0) or 0)
    if aid <= 0:
        return
    try:
        from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import release_interrupt_claim
        release_interrupt_claim(aid, enemy_sid, reason=str(reason or "not_fired"))
    except Exception:
        pass
    # The exact-cast lease and the legacy target lease are separate locks.
    # Releasing only the former still excluded teammates for another 800 ms.
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind

        email, gid = _owner_context()
        for _slot, intent in GLOBAL_CACHE.ShMem.GetAllAccounts().GetAllIntents() or []:
            if (
                str(intent.OwnerEmail) == email
                and int(intent.IsolationGroupID) == gid
                and int(intent.TargetAgentID) == aid
                and int(intent.KindID) == int(WhiteboardLockKind.SKILL_TARGET)
                and int(intent.SkillID) == int(_INTERRUPT_TARGET_LOCK)
            ):
                intent.reset()
    except Exception:
        pass


def interrupt_dispatch_options(
    target_id: int, our_skill_id: int, packet: CryPacket | None = None
) -> InterruptDispatchOptions:
    """Revalidate at queue execution; log issued commands, not queued plans."""
    aid, our_sid = int(target_id), int(our_skill_id)
    enemy_sid = int(_EXACT_INTERRUPT_RESERVED_CASTS.get((aid, our_sid), 0) or 0)
    reserved_at = int(_now_ms())
    caster_id = int(Player.GetAgentID())
    key = (aid, our_sid)
    token = object()
    _INTERRUPT_DISPATCH_TOKENS[key] = token

    def guard() -> bool:
        pi_protected_override = bool(
            int(our_sid) == int(Psychic_Instability_ID)
            and int(_PI_PROTECTED_OVERRIDE_CASTS.get(key, 0) or 0) == int(enemy_sid)
        )
        if (
            enemy_sid <= 0
            or not _valid_enemy(aid)
            or _INTERRUPT_DISPATCH_TOKENS.get(key) is not token
            or int(Player.GetAgentID()) != caster_id
            or int(_now_ms()) - reserved_at > int(INTERRUPT_RESERVATION_MS)
            or _EXACT_INTERRUPT_RESERVED_CASTS.get((aid, our_sid)) != enemy_sid
            or _casting_skill_id(aid) != enemy_sid
            or (not pi_protected_override and is_mistrust_covering_cast(aid, enemy_sid))
            or (not pi_protected_override and _is_overload_setup_protected(aid, enemy_sid))
            or not _interrupt_feasible(aid, our_sid)
        ):
            return False
        remaining = _interrupt_remaining_ms(aid, enemy_sid)
        # Normal selection already required the larger scheduling allowance.
        # The final dispatch still needs the emergency floor + native timing.
        minimum = _interrupt_min_remaining_ms(our_sid, emergency=True, enemy_skill_id=enemy_sid)
        if remaining is not None and int(remaining) < int(minimum):
            _log_interrupt_late_skip(aid, enemy_sid, our_sid, int(remaining), minimum)
            return False
        if our_sid == int(Cry_of_Frustration_ID):
            radius = float(GLOBAL_CACHE.Skill.Data.GetAoERange(our_sid) or Range.Nearby.value)
            if _cry_would_break_damage_setup(aid, radius, _interrupt_scan_enemy_ids()):
                return False
        return True

    def dispatched() -> None:
        _INTERRUPT_DISPATCH_TOKENS.pop(key, None)
        _PI_PROTECTED_OVERRIDE_CASTS.pop(key, None)
        if packet is not None:
            register_cry_interrupt_cast(packet, our_sid)
        else:
            register_single_interrupt_cast(aid, our_sid)

    def cancelled() -> None:
        # An old queued callback must never release a newer reservation.
        if _INTERRUPT_DISPATCH_TOKENS.get(key) is not token:
            return
        _INTERRUPT_DISPATCH_TOKENS.pop(key, None)
        _PI_PROTECTED_OVERRIDE_CASTS.pop(key, None)
        if our_sid == int(Complicate_ID):
            release_complicate_reservation(enemy_sid, target_id=aid, reason="queue_dispatch_invalidated")
        else:
            release_single_interrupt_reservation(aid, our_sid, reason="queue_dispatch_invalidated")
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug

            CombatDebug.log_event(
                "MESMERWAY_INTERRUPT_QUEUE_CANCELLED",
                target_id=aid,
                enemy_skill_id=enemy_sid,
                our_skill_id=our_sid,
                queue_age_ms=int(_now_ms()) - reserved_at,
            )
        except Exception:
            pass

    return {"dispatch_guard": guard, "on_dispatched": dispatched, "on_cancelled": cancelled}


def _interrupt_scan_enemy_ids() -> list[int]:
    """Live interrupt fallback pool inside local Spellcast range.

    The authoritative TeamCombatFocus packet remains the first-choice damage
    packet.  This broader pool is consulted only when that packet contains no
    *usable* interrupt target (for example every caster in it is protected by
    Mistrust, no one is currently casting, or all casts miss the timing gate).
    """
    try:
        enemies = list(AgentArray.GetEnemyArray() or [])
        enemies = AgentArray.Filter.ByDistance(
            enemies, Player.GetXY(), float(Range.Spellcast.value)
        )
        return sorted({int(aid) for aid in enemies if _valid_enemy(int(aid))})
    except Exception:
        return []


def _skill_cast_candidates(
    our_skill_id: int, *, spell_or_chant_only: bool, minimum_priority: int = -1,
    fallback_only: bool = False, emergency_timing: bool = False,
) -> list[tuple[int, int, int]]:
    """Return interrupt candidates with a strict cluster-first fallback.

    Stage 1.24 policy: if the authoritative packet has at least one usable cast,
    only that packet competes.  A lone/outside target may be selected only when
    the packet itself cannot be profitably interrupted.  Thus a four-foe packet
    wins over a single Frustration target, but if all four are Mistrust-protected
    the controller is free to hit the valid single caster instead.
    """
    _anchor, cluster_members = cluster_anchor_and_members()

    def _collect(members: list[int] | tuple[int, ...]) -> list[tuple[int, int, int]]:
        result: list[tuple[int, int, int]] = []
        for raw_aid in members:
            aid = int(raw_aid)
            sid = _casting_skill_id(aid)
            if sid <= 0:
                continue
            if spell_or_chant_only and not _is_spell_or_chant(sid):
                continue
            # Stage 1.25: protect only the concrete spell activation Mistrust is
            # confirmed to cover. Heals/self/untargeted spells remain interruptible.
            if is_mistrust_covering_cast(aid, sid):
                continue
            if _is_locked(_INTERRUPT_TARGET_LOCK, aid):
                try:
                    from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import release_unviable_interrupt_owner_claims
                    rescued = bool(release_unviable_interrupt_owner_claims(
                        aid, sid, skill_target_lock_id=int(_INTERRUPT_TARGET_LOCK)
                    ))
                except Exception:
                    rescued = False
                if not rescued and _is_locked(_INTERRUPT_TARGET_LOCK, aid):
                    continue

            priority = _interrupt_priority(aid, sid)
            if int(priority) < int(minimum_priority):
                continue
            # A non-emergency Overload setup gets only a few hundred milliseconds
            # to land. Serious heals/rez/AoE are never held back by this window.
            if _is_overload_setup_protected(aid, sid) and int(priority) < int(FRUSTRATION_DANGER_OVERRIDE_SCORE):
                continue
            if not _interrupt_feasible(aid, int(our_skill_id)):
                continue

            remaining = _interrupt_remaining_ms(aid, sid)
            minimum = _interrupt_min_remaining_ms(
                int(our_skill_id), emergency=bool(emergency_timing), enemy_skill_id=sid
            )
            if remaining is not None and minimum > 0 and int(remaining) < int(minimum):
                _log_interrupt_late_skip(aid, sid, int(our_skill_id), int(remaining), int(minimum))
                continue

            result.append((int(aid), int(sid), int(priority)))
        return result

    cluster_set = {int(aid) for aid in cluster_members}
    fallback_members = [
        int(aid) for aid in _interrupt_scan_enemy_ids()
        if int(aid) not in cluster_set
    ]

    # Strict first pass: the current authoritative damage packet owns interrupt
    # spam whenever it contains at least one usable target. Cry may explicitly
    # request fallback_only after discovering that every cluster-centred AoE
    # would break a protected Mistrust/Overload damage setup.
    if fallback_only:
        result = _collect(fallback_members)
    else:
        result = _collect(cluster_members)
        if not result:
            result = _collect(fallback_members)

    def _sort_key(item: tuple[int, int, int]) -> tuple[int, int, int, int, int, int]:
        aid, sid, priority = item
        remaining = _interrupt_remaining_ms(aid, sid)
        emergency = 1 if int(priority) >= int(FRUSTRATION_DANGER_OVERRIDE_SCORE) else 0
        frustration = 1 if is_frustration_focused(aid) else 0
        known = 1 if remaining is not None else 0
        headroom = int(remaining) if remaining is not None else -1
        # Inside the chosen scope: emergency safety first, then Frustration soft
        # focus, then ordinary danger/headroom. Scope selection itself already
        # guarantees cluster-before-single.
        return (emergency, int(priority), frustration, int(known), int(headroom), -int(aid))

    result.sort(key=_sort_key, reverse=True)
    return result


def _distance(a: int, b: int) -> float:
    try:
        ax, ay = Agent.GetXY(int(a)); bx, by = Agent.GetXY(int(b))
        return math.hypot(float(ax) - float(bx), float(ay) - float(by))
    except Exception:
        return 10**9


def _cry_would_break_damage_setup(center_id: int, radius: float, enemy_ids: list[int]) -> bool:
    """Do not let Cry's area interrupt consume protected damage setups.

    Cry of Frustration interrupts *all* foes in its area that are using a skill.
    Therefore checking only the primary target is insufficient: a valid centre
    could still accidentally interrupt a nearby Mistrust target before Mistrust
    pays out.  The same guard covers the short non-emergency Overload landing
    window. Frustration is intentionally NOT protected; it is an interrupt focus.
    """
    for raw_aid in enemy_ids:
        aid = int(raw_aid)
        if _distance(int(center_id), aid) > float(radius):
            continue
        sid = int(_casting_skill_id(aid) or 0)
        if sid <= 0:
            continue
        if is_mistrust_covering_cast(aid, sid):
            return True
        if _is_overload_setup_protected(aid, sid):
            return True
    return False


def reserve_cry_packet(*, mode: str = "standard") -> CryPacket | None:
    """Reserve a Cry target/packet with strict cluster-first damage policy.

    ``emergency`` accepts only high-danger casts and is used before eSurge.
    ``offensive`` runs after eSurge and treats Cry primarily as damage. A real
    cluster is always preferred over a lone target, but singles/outside casters
    become valid fallback when the cluster has no usable cast or every possible
    Cry centre would AoE-interrupt a protected Mistrust/Overload setup.
    Frustration is a positive focus bonus *inside* the chosen packet.
    """
    mode = str(mode or "standard").casefold()
    minimum_priority = int(FRUSTRATION_DANGER_OVERRIDE_SCORE) if mode == "emergency" else -1
    damage_pool = _interrupt_scan_enemy_ids()
    try:
        radius = float(GLOBAL_CACHE.Skill.Data.GetAoERange(Cry_of_Frustration_ID) or Range.Nearby.value)
    except Exception:
        radius = float(Range.Nearby.value)

    def _build_packets(casts: list[tuple[int, int, int]]) -> list[tuple]:
        packets: list[tuple] = []
        for aid, primary_sid, score in casts:
            # Cry interrupts all currently-acting foes in its area. Do not cast
            # through Mistrust or a non-emergency Overload landing window merely
            # to reach another target in the same clump.
            if _cry_would_break_damage_setup(int(aid), float(radius), damage_pool):
                continue

            covered_casts = tuple(sorted(
                (int(other_id), int(other_sid))
                for other_id, other_sid, _score in casts
                if _distance(aid, other_id) <= radius
            ))
            covered = tuple(sorted({enemy_id for enemy_id, _sid in covered_casts}))
            damage_ids = tuple(sorted(
                int(enemy_id) for enemy_id in damage_pool
                if _distance(aid, int(enemy_id)) <= radius
            ))
            aoe_count = max(1, len(damage_ids))
            frustration = 1 if is_frustration_focused(aid) else 0
            emergency = 1 if int(score) >= int(FRUSTRATION_DANGER_OVERRIDE_SCORE) else 0
            remaining = _interrupt_remaining_ms(aid, primary_sid)
            headroom = int(remaining) if remaining is not None else -1

            # Real AoE packet value comes before Frustration. Frustration remains
            # the preferred interrupt focus among equally valuable centres.
            packets.append((
                int(emergency), int(aoe_count), int(frustration),
                len(covered_casts), int(score), int(headroom), -int(aid),
                int(aid), int(primary_sid), covered, covered_casts, damage_ids,
            ))
        packets.sort(reverse=True)
        return packets

    casts = _skill_cast_candidates(
        Cry_of_Frustration_ID, spell_or_chant_only=False,
        minimum_priority=minimum_priority,
    )
    packets = _build_packets(casts)

    # If the authoritative packet exists but Cry cannot safely fire there (most
    # importantly: Mistrust-protected casters are currently acting), fall back to
    # a valid single/outside target rather than wasting the ready interrupt.
    if not packets:
        fallback_casts = _skill_cast_candidates(
            Cry_of_Frustration_ID, spell_or_chant_only=False,
            minimum_priority=minimum_priority, fallback_only=True,
        )
        packets = _build_packets(fallback_casts)

    for (_emergency, _aoe, _frustration, _covered_count, _score, _headroom,
         _neg_aid, aid, primary_sid, covered, covered_casts, damage_ids) in packets:
        if not _claim_exact_interrupt(aid, primary_sid, Cry_of_Frustration_ID):
            continue
        if not _try_lock(_INTERRUPT_TARGET_LOCK, aid, INTERRUPT_RESERVATION_MS):
            _release_exact_interrupt(aid, Cry_of_Frustration_ID, reason="mesmerway_target_lock_lost")
            continue
        for other_id in covered:
            if other_id != aid:
                _try_lock(_INTERRUPT_TARGET_LOCK, other_id, INTERRUPT_RESERVATION_MS)
        return CryPacket(
            target_id=int(aid), covered_enemy_ids=tuple(covered),
            covered_casts=tuple(covered_casts), damage_enemy_ids=tuple(damage_ids),
            danger_score=int(_score), frustration_focus=bool(_frustration), mode=str(mode),
        )
    return None


def _has_mesmer_hex(agent_id: int) -> bool:
    try:
        for sid in _effect_ids(int(agent_id)):
            if sid <= 0 or not GLOBAL_CACHE.Skill.Flags.IsHex(sid):
                continue
            profession_id, _secondary = GLOBAL_CACHE.Skill.GetProfession(sid)
            if int(getattr(profession_id, "value", profession_id) or 0) == int(Profession.Mesmer.value):
                return True
    except Exception:
        return False
    return False


def reserve_overload_target() -> int:
    """Use Overload as setup damage on long, non-emergency live activations.

    Mistrust targets are left alone for setup-spreading purposes. A short setup lease lets Overload land before
    our own offensive Cry/Power Spike, but danger-score >=96 ignores that lease.
    Frustration targets and dense packets are preferred.
    """
    _anchor, members = cluster_anchor_and_members()
    rows: list[tuple[int, int, int, int, float, int]] = []
    for aid in members:
        if (_is_locked(_OVERLOAD_TARGET_LOCK, aid) or is_mistrust_protected(aid)
                or is_fragility_feeder_window(aid)):
            continue
        sid = _casting_skill_id(aid)
        if sid <= 0:
            continue
        priority = int(_interrupt_priority(aid, sid))
        if priority >= int(FRUSTRATION_DANGER_OVERRIDE_SCORE):
            continue
        remaining = _interrupt_remaining_ms(aid, sid)
        if remaining is not None and int(remaining) < int(OVERLOAD_SETUP_MIN_REMAINING_MS):
            continue
        try:
            hp = float(Agent.GetHealth(aid) or 0.0)
        except Exception:
            hp = 1.0
        if hp <= 0.20:
            continue
        density = 0
        try:
            from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
            density = int(TeamCombatFocus.count_adjacent_enemies(aid, Range.Adjacent.value))
        except Exception:
            pass
        focus = 1 if is_frustration_focused(aid) else 0
        rows.append((focus, int(density), int(priority), int(remaining or 0), float(hp), int(aid)))
    rows.sort(reverse=True)
    for _focus, _density, _priority, _remaining, _hp, aid in rows:
        if _try_lock(_OVERLOAD_TARGET_LOCK, aid, FILLER_TARGET_RESERVATION_MS):
            _try_lock(_OVERLOAD_SETUP_LOCK, aid, OVERLOAD_SETUP_LOCK_MS)
            return int(aid)
    return 0


def reserve_cry_of_pain_target() -> int:
    """Reserve Cry of Pain only for a live Mesmer-hexed multi-foe packet.

    Preserve Stage 1.43: Cry of Pain owns the 2+ foe packet-damage niche before
    Complicate, while lone casts remain available to specialist interrupts.
    """
    candidates = _skill_cast_candidates(Cry_of_Pain_ID, spell_or_chant_only=False)
    scored: list[tuple[int, int, int]] = []
    for aid, _sid, priority in candidates:
        if not _has_mesmer_hex(aid):
            continue
        density = 0
        try:
            from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
            density = int(TeamCombatFocus.count_adjacent_enemies(aid, Range.Adjacent.value))
        except Exception:
            pass
        if int(density) < 2:
            continue
        scored.append((density, priority, aid))
    scored.sort(reverse=True)
    for _density, _priority, aid in scored:
        if _try_lock(_INTERRUPT_TARGET_LOCK, aid, INTERRUPT_RESERVATION_MS):
            return int(aid)
    return 0


def reserve_priority_pi_interrupt_target(
    target_id: int, *, allow_protected_override: bool = False
) -> bool:
    """Reserve one caller-selected live activation for Psychic Instability.

    A 3+ Nearby PI packet may deliberately override Mistrust/Overload setup
    protection because the requested policy values the multi-target knockdown
    above a single protected proc. Small-packet martial/cleanup PI continues to
    respect those protections. Exact-cast ownership and native feasibility are
    always required, so PI still cannot double-interrupt another committed cast.
    """
    aid = int(target_id or 0)
    if aid <= 0 or int(Psychic_Instability_ID or 0) <= 0:
        return False
    sid = int(_casting_skill_id(aid) or 0)
    if sid <= 0:
        return False
    if _is_locked(_INTERRUPT_TARGET_LOCK, aid):
        try:
            from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import release_unviable_interrupt_owner_claims
            rescued = bool(release_unviable_interrupt_owner_claims(
                aid, sid, skill_target_lock_id=int(_INTERRUPT_TARGET_LOCK)
            ))
        except Exception:
            rescued = False
        if not rescued and _is_locked(_INTERRUPT_TARGET_LOCK, aid):
            return False
    if not bool(allow_protected_override):
        if is_mistrust_covering_cast(aid, sid):
            return False
        priority = int(_interrupt_priority(aid, sid))
        if _is_overload_setup_protected(aid, sid) and priority < int(FRUSTRATION_DANGER_OVERRIDE_SCORE):
            return False
    if not _interrupt_feasible(aid, int(Psychic_Instability_ID)):
        return False
    remaining = _interrupt_remaining_ms(aid, sid)
    minimum = _interrupt_min_remaining_ms(int(Psychic_Instability_ID))
    if remaining is not None and int(remaining) < int(minimum):
        _log_interrupt_late_skip(aid, sid, int(Psychic_Instability_ID), int(remaining), int(minimum))
        return False
    if not _claim_exact_interrupt(aid, sid, int(Psychic_Instability_ID)):
        return False
    if _try_lock(_INTERRUPT_TARGET_LOCK, aid, INTERRUPT_RESERVATION_MS):
        if bool(allow_protected_override):
            _PI_PROTECTED_OVERRIDE_CASTS[(int(aid), int(Psychic_Instability_ID))] = int(sid)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_PI_EXACT_CLAIM", caster_id=int(aid), enemy_skill_id=int(sid),
                protected_override=bool(allow_protected_override),
                policy="stage1_72_pi_cluster_first_refusal_exact_cast_claim",
            )
        except Exception:
            pass
        return True
    _release_exact_interrupt(aid, int(Psychic_Instability_ID), reason="mesmerway_pi_target_lock_lost")
    return False


def reserve_specific_interrupt_target(target_id: int, our_skill_id: int) -> bool:
    """Reserve one exact live cast on a caller-selected target.

    Used by the <=10% cleanup path so Cry can execute a lone caster without
    bypassing the same Mistrust, Overload-setup, feasibility, timing and
    cross-account claim rules as the normal interrupt package.
    """
    aid = int(target_id or 0)
    skill_id = int(our_skill_id or 0)
    if aid <= 0 or skill_id <= 0:
        return False
    sid = int(_casting_skill_id(aid) or 0)
    if sid <= 0:
        return False
    if _is_locked(_INTERRUPT_TARGET_LOCK, aid):
        try:
            from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import release_unviable_interrupt_owner_claims
            rescued = bool(release_unviable_interrupt_owner_claims(
                aid, sid, skill_target_lock_id=int(_INTERRUPT_TARGET_LOCK)
            ))
        except Exception:
            rescued = False
        if not rescued and _is_locked(_INTERRUPT_TARGET_LOCK, aid):
            return False
    if is_mistrust_covering_cast(aid, sid):
        return False
    priority = int(_interrupt_priority(aid, sid))
    if _is_overload_setup_protected(aid, sid) and priority < int(FRUSTRATION_DANGER_OVERRIDE_SCORE):
        return False
    if not _interrupt_feasible(aid, skill_id):
        return False
    remaining = _interrupt_remaining_ms(aid, sid)
    minimum = _interrupt_min_remaining_ms(skill_id, enemy_skill_id=sid)
    if remaining is not None and minimum > 0 and int(remaining) < int(minimum):
        _log_interrupt_late_skip(aid, sid, skill_id, int(remaining), int(minimum))
        return False
    if not _claim_exact_interrupt(aid, sid, skill_id):
        return False
    if _try_lock(_INTERRUPT_TARGET_LOCK, aid, INTERRUPT_RESERVATION_MS):
        return True
    _release_exact_interrupt(aid, skill_id, reason="mesmerway_specific_target_lock_lost")
    return False


def reserve_single_interrupt_target(our_skill_id: int, *, emergency_only: bool = False) -> int:
    """Power Spike/Power Drain share one lock but keep their real trigger types."""
    skill_id = int(our_skill_id)
    candidates = _skill_cast_candidates(
        # Check the actual interrupt trigger before cluster-first selection.
        # Otherwise a non-spell cast inside the primary AoE packet may hide a
        # valid healer/support spell from the fallback pool.
        skill_id,
        spell_or_chant_only=skill_id in (int(Power_Spike_ID), int(Power_Drain_ID)),
        minimum_priority=(int(FRUSTRATION_DANGER_OVERRIDE_SCORE) if emergency_only else -1),
        emergency_timing=bool(emergency_only),
    )
    for aid, enemy_skill_id, _score in candidates:
        try:
            flags = GLOBAL_CACHE.Skill.Flags
            if skill_id == int(Power_Spike_ID) and not (
                flags.IsSpell(int(enemy_skill_id)) or flags.IsChant(int(enemy_skill_id))
            ):
                continue
            if skill_id == int(Power_Drain_ID) and not (
                flags.IsSpell(int(enemy_skill_id)) or flags.IsChant(int(enemy_skill_id))
            ):
                continue
        except Exception:
            continue
        if not _claim_exact_interrupt(aid, enemy_skill_id, skill_id):
            continue
        if _try_lock(_INTERRUPT_TARGET_LOCK, aid, INTERRUPT_RESERVATION_MS):
            return int(aid)
        _release_exact_interrupt(aid, skill_id, reason="mesmerway_target_lock_lost")
    return 0



def reserve_tactical_power_drain_target(*, critical_only: bool = False) -> int:
    """Stage 2.59-PD experiment: reserve a *valuable live spell* for Power Drain.

    The normal low-energy lane remains unchanged. This optional early lane
    considers healer/support casts, high-impact spells and Frustration targets.
    It uses the same exact-cast and cross-client locks as all other interrupts.
    A separately positioned healer is a fallback when the current cluster has
    no tactical candidate; this never initiates movement or changes DPS focus.
    """
    def useful(row: tuple[int, int, int]) -> bool:
        aid, enemy_sid, priority = row
        try:
            flags = GLOBAL_CACHE.Skill.Flags
            if not (flags.IsSpell(int(enemy_sid)) or flags.IsChant(int(enemy_sid))):
                return False
        except Exception:
            return False
        remaining = _interrupt_remaining_ms(int(aid), int(enemy_sid))
        # A 1/4-second activation still needs network/queue headroom.
        if remaining is not None and int(remaining) < 650:
            return False
        # Above 85% own energy, only truly dangerous casts justify spending
        # an interrupt purely for control instead of its energy recovery.
        if critical_only:
            return int(priority) >= int(FRUSTRATION_DANGER_OVERRIDE_SCORE)
        if int(priority) >= 82 or is_frustration_focused(int(aid)):
            return True
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            scores = EnemyKnowledge.role_scores(int(aid))
            return int(scores.get('healer', 0)) >= 85 or int(scores.get('support', 0)) >= 85
        except Exception:
            return False

    candidates = [row for row in _skill_cast_candidates(
        Power_Drain_ID, spell_or_chant_only=True,
    ) if useful(row)]
    if not candidates:
        candidates = [row for row in _skill_cast_candidates(
            Power_Drain_ID, spell_or_chant_only=True, fallback_only=True,
        ) if useful(row)]
    for aid, enemy_sid, _priority in candidates:
        if not _claim_exact_interrupt(aid, enemy_sid, Power_Drain_ID):
            continue
        if _try_lock(_INTERRUPT_TARGET_LOCK, aid, INTERRUPT_RESERVATION_MS):
            return int(aid)
        _release_exact_interrupt(aid, Power_Drain_ID, reason='tactical_power_drain_lock_lost')
    return 0


def register_cry_interrupt_cast(packet: CryPacket, our_skill_id: int = Cry_of_Frustration_ID) -> None:
    if packet is None:
        return
    _EXACT_INTERRUPT_RESERVED_CASTS.pop((int(packet.target_id), int(our_skill_id)), None)
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        for enemy_id, enemy_skill_id in tuple(packet.covered_casts or ()):
            if int(enemy_skill_id or 0) > 0:
                CombatDebug.register_interrupt_fired(int(enemy_id), int(enemy_skill_id), int(our_skill_id))
    except Exception:
        pass


def interrupt_dispatch_snapshot(target_id: int, our_skill_id: int) -> dict[str, int | bool | None]:
    """Small diagnostic snapshot for one exact direct-interrupt dispatch."""
    aid = int(target_id or 0)
    our_sid = int(our_skill_id or 0)
    enemy_sid = int(_EXACT_INTERRUPT_RESERVED_CASTS.get((aid, our_sid), 0) or 0)
    if enemy_sid <= 0:
        enemy_sid = int(_casting_skill_id(aid) or 0)
    remaining = _interrupt_remaining_ms(aid, enemy_sid) if enemy_sid > 0 else None
    return {
        "enemy_skill_id": int(enemy_sid),
        "remaining_ms": None if remaining is None else int(remaining),
        "priority": int(_interrupt_priority(aid, enemy_sid)) if enemy_sid > 0 else 0,
        "frustration_focus": bool(is_frustration_focused(aid)),
        "mistrust_covering": bool(is_mistrust_covering_cast(aid, enemy_sid)) if enemy_sid > 0 else False,
    }


def register_single_interrupt_cast(target_id: int, our_skill_id: int) -> None:
    aid = int(target_id or 0)
    our_sid = int(our_skill_id or 0)
    if aid <= 0:
        return
    _PI_PROTECTED_OVERRIDE_CASTS.pop((aid, our_sid), None)
    sid = int(_EXACT_INTERRUPT_RESERVED_CASTS.pop((aid, our_sid), 0) or 0)
    if sid <= 0:
        sid = _casting_skill_id(aid)
    if sid <= 0:
        return
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.register_interrupt_fired(aid, int(sid), int(our_sid))
    except Exception:
        pass


def release_single_interrupt_reservation(target_id: int, our_skill_id: int, *, reason: str = "not_fired") -> None:
    _release_exact_interrupt(int(target_id), int(our_skill_id), reason=str(reason))


def release_cry_interrupt_reservation(packet: CryPacket | None, *, reason: str = "not_fired") -> None:
    if packet is None:
        return
    _release_exact_interrupt(
        int(packet.target_id), int(Cry_of_Frustration_ID), reason=str(reason)
    )


def is_interrupt_reserved(agent_id: int) -> bool:
    """True while another Mesmerway interrupt already owns this enemy."""
    return bool(_is_locked(_INTERRUPT_TARGET_LOCK, int(agent_id or 0)))


def _complicate_same_skill_value(target_id: int, enemy_skill_id: int) -> int:
    """Count nearby foes known to carry the same skill Complicate would disable."""
    aid = int(target_id or 0)
    sid = int(enemy_skill_id or 0)
    if aid <= 0 or sid <= 0:
        return 1
    _anchor, members = cluster_anchor_and_members()
    try:
        radius = float(GLOBAL_CACHE.Skill.Data.GetAoERange(Complicate_ID) or Range.Area.value)
    except Exception:
        radius = float(Range.Area.value)
    count = 0
    try:
        from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
        for other_id in members:
            oid = int(other_id)
            if _distance(aid, oid) > radius:
                continue
            if sid in EnemyKnowledge.known_skills(oid):
                count += 1
    except Exception:
        count = 0
    # The caster demonstrably owns the skill even when offline knowledge is absent.
    return max(1, int(count))


def reserve_complicate_target(*, emergency_only: bool = False) -> tuple[int, int]:
    """Reserve Complicate only when its long disable has real strategic value.

    Unlike Cry/Power Spike, Complicate has no direct-damage payoff. Stage 1.25
    therefore spends the single team copy on danger/heal/protection/shutdown or
    on a skill known to exist on multiple nearby foes. Unknown healer/support
    casters keep a conservative fallback so the knowledge library is advisory.
    """
    candidates = _skill_cast_candidates(
        Complicate_ID, spell_or_chant_only=False, emergency_timing=bool(emergency_only)
    )
    if not candidates:
        return 0, 0

    rows: list[tuple[int, int, int, int, int, int, int, int, int, int, int]] = []
    for aid, sid, priority in candidates:
        healer = support = caster = 0
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            scores = EnemyKnowledge.role_scores(int(aid))
            healer = int(scores.get("healer", 0))
            support = int(scores.get("support", 0))
            caster = int(scores.get("caster", 0))
        except Exception:
            pass
        category = "unknown"
        try:
            from Py4GWCoreLib.Builds.Skills import DangerousSkillPriorities as DSP
            category = str(DSP.get_category(int(sid), "unknown") or "unknown")
        except Exception:
            pass
        same_count = int(_complicate_same_skill_value(int(aid), int(sid)))
        dangerous = 1 if int(priority) >= int(FRUSTRATION_DANGER_OVERRIDE_SCORE) else 0
        if emergency_only and not dangerous:
            continue

        category_value = 1 if category in {
            "resurrection", "lethal_aoe", "hard_protection", "protection",
            "party_heal", "major_heal", "heal_cleanse", "hard_shutdown",
            "shutdown", "control",
        } else 0
        duplicate_value = 1 if same_count >= 2 else 0
        knowledge_value = 1 if int(priority) >= 80 else 0
        unknown_healer_fallback = 1 if (
            category == "unknown" and (int(healer) >= 70 or int(support) >= 80)
        ) else 0
        if not emergency_only and not (
            dangerous or category_value or duplicate_value or knowledge_value or unknown_healer_fallback
        ):
            continue

        frustration = 1 if is_frustration_focused(aid) else 0
        rows.append((
            dangerous, int(category_value), int(duplicate_value), int(same_count),
            frustration, int(healer), int(support), int(priority), int(caster),
            -int(aid), int(sid),
        ))
    rows.sort(reverse=True)

    for (_dangerous, _category_value, _duplicate_value, _same_count, _frustration,
         _healer, _support, _priority, _caster, neg_aid, sid) in rows:
        aid = -int(neg_aid)
        skill_reserved = False
        try:
            from Py4GWCoreLib.Builds.Skills.PIMesmerCoordination import reserve_complicate_skill
            skill_reserved = bool(reserve_complicate_skill(int(sid)))
        except Exception:
            skill_reserved = True
        if not skill_reserved:
            continue
        if not _claim_exact_interrupt(int(aid), int(sid), int(Complicate_ID)):
            try:
                from Py4GWCoreLib.Builds.Skills.PIMesmerCoordination import release_complicate_skill
                release_complicate_skill(int(sid), reason="mesmerway_exact_claim_lost")
            except Exception:
                pass
            continue
        if _try_lock(_INTERRUPT_TARGET_LOCK, int(aid), INTERRUPT_RESERVATION_MS):
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_COMPLICATE_VALUE", target_id=int(aid),
                    enemy_skill_id=int(sid), category=str(category),
                    same_skill_known_nearby=int(_complicate_same_skill_value(aid, sid)),
                    policy="stage1_25_value_disable_not_generic_spam",
                )
            except Exception:
                pass
            return int(aid), int(sid)
        _release_exact_interrupt(int(aid), int(Complicate_ID), reason="mesmerway_target_lock_lost")
        try:
            from Py4GWCoreLib.Builds.Skills.PIMesmerCoordination import release_complicate_skill
            release_complicate_skill(int(sid), reason="mesmerway_target_lock_lost")
        except Exception:
            pass
    return 0, 0

def release_complicate_reservation(
    enemy_skill_id: int, *, target_id: int = 0, reason: str = "not_fired"
) -> None:
    if int(target_id or 0) > 0:
        _release_exact_interrupt(int(target_id), int(Complicate_ID), reason=str(reason))
    try:
        from Py4GWCoreLib.Builds.Skills.PIMesmerCoordination import release_complicate_skill
        release_complicate_skill(int(enemy_skill_id), reason=str(reason))
    except Exception:
        pass


def reserve_shatter_enchantment_target(*, cleanup_target_id: int = 0) -> int:
    """Pick an enchanted enemy in the authoritative packet, one copy at a time."""
    anchor, members = cluster_anchor_and_members()
    if not members:
        return 0
    rows: list[tuple[int, int, int, float, int]] = []
    for aid in members:
        if int(cleanup_target_id or 0) > 0 and int(aid) != int(cleanup_target_id):
            continue
        if _is_locked(_SHATTER_ENCHANT_TARGET_LOCK, aid):
            continue
        try:
            if not Agent.IsEnchanted(int(aid)):
                continue
            hp = float(Agent.GetHealth(int(aid)) or 0.0)
        except Exception:
            continue
        if hp <= 0.15 and int(aid) != int(cleanup_target_id or 0):
            continue
        role = 0
        threat = 0
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            scores = EnemyKnowledge.role_scores(int(aid))
            role = max(
                int(scores.get("healer", 0)),
                int(scores.get("support", 0)),
                int(scores.get("caster", 0)),
            )
            threat = int(EnemyKnowledge.threat_bonus(int(aid)))
        except Exception:
            pass
        rows.append((int(aid == anchor), int(role), int(threat), float(hp), -int(aid)))
    rows.sort(reverse=True)
    for _anchor_first, _role, _threat, _hp, neg_aid in rows:
        aid = -int(neg_aid)
        if _try_lock(_SHATTER_ENCHANT_TARGET_LOCK, aid, ENCHANT_REMOVAL_PENDING_MS):
            return int(aid)
    return 0


def _daze_remaining_ms(agent_id: int) -> int:
    try:
        if int(Dazed_ID or 0) <= 0:
            return 0
        if not Routines.Checks.Agents.HasEffect(int(agent_id), int(Dazed_ID)):
            return 0
        return max(0, int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(int(agent_id), int(Dazed_ID)) or 0))
    except Exception:
        return 0


def _relevant_caster(agent_id: int) -> bool:
    try:
        from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
        scores = EnemyKnowledge.role_scores(int(agent_id))
        return bool(
            int(scores.get("caster", 0)) >= 55
            or int(scores.get("support", 0)) >= 70
            or int(scores.get("healer", 0)) >= 70
        )
    except Exception:
        try:
            return bool(Agent.IsCaster(int(agent_id)))
        except Exception:
            return False


def reserve_fragility_target() -> int:
    """Reserve one central Fragility packet for condition value + Shatter fuel.

    Stage 1.36 no longer requires packet members to be free of older Mesmer
    hexes. Fragility is intentionally applied *after* those setups, a pending
    feeder lease blocks new covers during dispatch, and a confirmed active lease
    lets Shatter remove the fresh Fragility while preserving older value.
    """
    anchor, members = cluster_anchor_and_members(consumer_role="fragility_setup")
    if not members or int(Fragility_ID or 0) <= 0:
        return 0
    primary_focus = int(_team_primary_focus_target(consumer_role="fragility_primary_focus") or 0)
    ready_shatters = int(_ready_skill_holders_count(Shatter_Delusions_ID) or 0)
    rows: list[tuple[int, int, int, int, int, int, float, int]] = []
    packets: dict[int, tuple[int, ...]] = {}
    for candidate in members:
        candidate = int(candidate)
        if (not _valid_hex_enemy(candidate) or _is_locked(_FRAGILITY_TARGET_LOCK, candidate)
                or _is_locked(_HEX_MUTATION_LOCK, candidate)
                or is_optional_shatter_pipeline_pending(candidate)):
            continue
        try:
            hp = max(0.0, float(Agent.GetHealth(candidate) or 0.0))
        except Exception:
            continue
        if hp <= 0.15:
            continue
        affected = _fragility_packet_ids(candidate)
        if not affected:
            continue
        # A prepared Web/Phantom conversion is already paid for and fast. Do
        # not overlay a new Fragility packet before that optional feeder gets a
        # chance to Shatter; the dispatch guard repeats this race-safe check.
        if any(is_optional_shatter_pipeline_pending(int(eid)) for eid in affected):
            continue
        fresh: list[int] = []
        for eid in affected:
            try:
                already_fragile = Routines.Checks.Agents.HasEffect(int(eid), int(Fragility_ID))
            except Exception:
                already_fragile = False
            if not already_fragile:
                fresh.append(int(eid))
        if not fresh:
            continue

        attackers = 0
        casters = 0
        for eid in fresh:
            try:
                attackers += 1 if Agent.IsAttacking(int(eid)) else 0
            except Exception:
                pass
            casters += 1 if _relevant_caster(int(eid)) else 0

        feeder_slots = min(int(len(fresh)), int(ready_shatters)) if ready_shatters > 0 else 0
        packets[int(candidate)] = tuple(int(v) for v in affected)
        rows.append((
            1 if int(candidate) == int(primary_focus) else 0,
            int(feeder_slots), int(len(fresh)), int(len(affected)),
            int(attackers), int(casters), float(hp), -int(candidate)
        ))
    rows.sort(reverse=True)
    for _is_primary, _feeder_slots, _fresh, _affected, _attackers, _casters, _hp, neg_candidate in rows:
        candidate = -int(neg_candidate)
        if _try_lock(_FRAGILITY_TARGET_LOCK, candidate, FRAGILITY_TARGET_RESERVATION_MS):
            pending = _mark_fragility_feeder_pending(candidate)
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_FRAGILITY_FEEDER_RESERVED",
                    center_target_id=int(candidate),
                    packet_size=len(pending),
                    ready_shatters=int(ready_shatters),
                    policy="stage1_36_pending_packet_blocks_new_hex_covers",
                )
            except Exception:
                pass
            return int(candidate)
    return 0


def is_martial_attack_target(aid: int) -> bool:
    """Robust martial classifier shared by account and native HeroAI Mesmers.

    Agent.IsMelee/Ranged only reflects live animation/classification; it was
    False for every Frustration target in the Stage 2.55 Urgoz logs. Fall back
    to live profession, cached enemy classification and actual weapon category.
    """
    aid = int(aid or 0)
    if aid <= 0:
        return False
    try:
        if bool(Agent.IsMelee(aid) or Agent.IsRanged(aid)):
            return True
    except Exception:
        pass
    martial_professions = {1, 2, 7, 9, 10}  # Warrior Ranger Assassin Paragon Dervish
    try:
        primary, _secondary = Agent.GetProfessions(aid)
        if int(getattr(primary, 'value', primary) or 0) in martial_professions:
            return True
    except Exception:
        pass
    try:
        from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
        rec = EnemyKnowledge.observe(aid, 0)
        if int(getattr(rec, 'primary', 0) or 0) in martial_professions:
            return True
    except Exception:
        pass
    try:
        return int(Agent.GetWeaponType(aid)[0] or 0) in {1, 2, 3, 4, 5, 6, 7}
    except Exception:
        return False


def reserve_frustration_target(*, origin_id: int = 0) -> int:
    """Prefer healer/support/dangerous caster interruption; attacks are fallback.

    Auto-attack interrupts activate Frustration. Avoid targets with existing
    Frustration / overlapping Mistrust, near-death, and already reserved foes.
    Never leave the canonical engaged packet.
    """
    _anchor, members = cluster_anchor_and_members()
    if not members or int(Frustration_ID or 0) <= 0:
        return 0

    rows: list[tuple] = []
    for aid in members:
        aid = int(aid)
        if not _valid_enemy(aid) or _is_locked(_FRUSTRATION_TARGET_LOCK, aid):
            continue
        # For native heroes use their own position, not the leading player's.
        if origin_id:
            try:
                if Utils.Distance(Agent.GetXY(int(origin_id)), Agent.GetXY(aid)) > float(Range.Spellcast.value):
                    continue
            except Exception:
                continue
        elif not _spellcast_reachable(aid):
            continue
        if is_fragility_feeder_window(aid) or is_optional_shatter_pipeline_pending(aid):
            continue
        try:
            hp = float(Agent.GetHealth(aid) or 0.0)
        except Exception:
            hp = 0.0
        if hp <= 0.20:
            continue
        try:
            if Routines.Checks.Agents.HasEffect(aid, int(Frustration_ID)):
                continue
            if int(Arcane_Conundrum_ID or 0) > 0 and Routines.Checks.Agents.HasEffect(aid, int(Arcane_Conundrum_ID)):
                continue
        except Exception:
            pass
        if is_mistrust_protected(aid):
            continue
        # Physical attack interrupts still trigger Frustration, but the five-
        # Mesmer team's separate anti-martial spells already handle these.
        # Prioritize healers whose activations the team can interrupt.
        attacking = False
        martial = False
        setup = False
        try:
            attacking = bool(Agent.IsAttacking(aid))
            try:
                martial = is_martial_attack_target(aid)
            except NameError:  # lightweight offline selector tests extract this function alone
                martial = bool(Agent.IsMelee(aid) or Agent.IsRanged(aid))
            setup = any(
                int(sid or 0) > 0 and Routines.Checks.Agents.HasEffect(aid, int(sid))
                for sid in (Clumsiness_ID, Wandering_Eye_ID)
            )
        except Exception:
            pass
        # Monster weapon/profession metadata can report no martial class even
        # while an attack animation is active. Clumsiness/Wandering Eye react to
        # the *attack*, not to the classifier; reserve a valid attack regardless
        # of whether its weapon category was identified.
        attack_combo_value = bool(attacking)
        martial_value = bool(martial and attacking)
        # Daze already supplies heavy cast-time pressure; spreading Frustration
        # to another caster yields more team-wide shutdown and interrupt value.
        if _daze_remaining_ms(aid) >= 1500:
            continue

        caster = healer = support = threat = 0
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            scores = EnemyKnowledge.role_scores(aid)
            caster = int(scores.get("caster", 0))
            healer = int(scores.get("healer", 0))
            support = int(scores.get("support", 0))
            threat = int(EnemyKnowledge.threat_bonus(aid))
        except Exception:
            pass
        if max(caster, healer, support) < 50 and not attack_combo_value:
            try:
                if not Agent.IsCaster(aid):
                    continue
            except Exception:
                continue

        enemy_sid = _casting_skill_id(aid)
        danger = _interrupt_priority(aid, enemy_sid) if enemy_sid > 0 else 0
        casting = 1 if enemy_sid > 0 else 0
        # Live dangerous cast first; otherwise protect the party from healers and
        # supports before generic casters.  Higher HP wins late ties so the hex
        # has time to generate more than one interrupt opportunity.
        caster_shutdown = int(max(healer, support, caster) >= 70)
        dangerous_caster = int(danger > 0 and max(healer, support, caster) >= 50)
        # A confirmed live healer/support cast beats an attack combo; against
        # an idle caster without proven heal pressure a *primed* attack combo
        # is a reliable immediate damage trigger. Otherwise control the healer.
        rows.append((dangerous_caster, int(caster_shutdown and casting),
                     int(attack_combo_value and setup), caster_shutdown,
                     int(healer), int(support), int(caster), int(threat),
                     int(martial_value), int(attack_combo_value), float(hp), -aid))

    rows.sort(reverse=True)
    for *_rank, neg_aid in rows:
        aid = -int(neg_aid)
        if _try_lock(_FRUSTRATION_TARGET_LOCK, aid, FRUSTRATION_TARGET_RESERVATION_MS):
            return int(aid)
    return 0


def reserve_diversion_target(*, origin_id: int = 0) -> int:
    """Predictive shutdown on a healthy caster/support, not a late react-cast.

    The next skill (not the one currently being cast) is disabled by Diversion.
    It is a short window and might be spent on a low-value action; limit casts
    to important casters in the authoritative engaged packet.  Cross-account
    lease prevents two Panic holders from hexing the same enemy concurrently.
    """
    _anchor, members = cluster_anchor_and_members()
    if int(Diversion_ID or 0) <= 0 or not members:
        return 0
    rows = []
    for aid in members:
        aid = int(aid)
        if not _valid_enemy(aid) or _is_locked(_DIVERSION_TARGET_LOCK, aid):
            continue
        if origin_id:
            try:
                if Utils.Distance(Agent.GetXY(int(origin_id)), Agent.GetXY(aid)) > float(Range.Spellcast.value):
                    continue
            except Exception:
                continue
        elif not _spellcast_reachable(aid):
            continue
        try:
            hp = float(Agent.GetHealth(aid) or 0.0)
        except Exception:
            continue
        if hp < 0.38 or is_mistrust_protected(aid):
            continue
        try:
            if Routines.Checks.Agents.HasEffect(aid, int(Diversion_ID)):
                continue
        except Exception:
            pass
        healer = support = caster = threat = 0
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            scores = EnemyKnowledge.role_scores(aid)
            healer = int(scores.get('healer', 0))
            support = int(scores.get('support', 0))
            caster = int(scores.get('caster', 0))
            threat = int(EnemyKnowledge.threat_bonus(aid))
        except Exception:
            pass
        if max(healer, support, caster) < 65:
            continue
        # High-priority healers first; live dangerous casts are handled by the
        # actual interrupts before Diversion reaches the controller's rotation.
        rows.append((healer, support, caster, threat, hp, -aid))
    rows.sort(reverse=True)
    for *_rank, neg_aid in rows:
        aid = -int(neg_aid)
        if _try_lock(_DIVERSION_TARGET_LOCK, aid, DIVERSION_TARGET_RESERVATION_MS):
            return aid
    return 0


def reserve_technobabble_target() -> int:
    """Smart Technobabble centre for whichever Mesmer equips it.

    It stays in the exact packet, refuses boss centres, existing Mistrust
    trigger packets and already-owned direct interrupts, and prefers a centre
    that applies fresh Daze to multiple relevant casters.  This is pre-emptive
    shutdown, not a replacement for Panic/Cry/Complicate.
    """
    _anchor, members = cluster_anchor_and_members()
    if not members:
        return 0
    try:
        from Py4GWCoreLib.Py4GWcorelib import Utils
    except Exception:
        return 0

    rows: list[tuple[int, int, int, int, int]] = []
    for candidate in members:
        candidate = int(candidate)
        if _is_locked(_TECHNOBABBLE_TARGET_LOCK, candidate):
            continue
        try:
            if Agent.HasBossGlow(candidate):
                continue
            cxy = Agent.GetXY(candidate)
            affected = [
                int(eid) for eid in members
                if Utils.Distance(cxy, Agent.GetXY(int(eid))) <= float(Range.Adjacent.value)
            ]
        except Exception:
            affected = [candidate]

        if any(is_mistrust_protected(eid) for eid in affected):
            continue
        if any(is_interrupt_reserved(eid) for eid in affected):
            continue

        fresh_daze = sum(1 for eid in affected if _daze_remaining_ms(eid) <= 1400)
        caster_count = sum(1 for eid in affected if _relevant_caster(eid))
        casting_count = sum(1 for eid in affected if _casting_skill_id(eid) > 0)
        if len(members) > 1:
            if fresh_daze < 2 or caster_count < 1:
                continue
        elif fresh_daze < 1 or caster_count < 1:
            continue
        rows.append((int(caster_count), int(fresh_daze), int(casting_count), len(affected), -candidate))

    rows.sort(reverse=True)
    for _casters, _fresh, _casting, _affected, neg_candidate in rows:
        candidate = -int(neg_candidate)
        if _try_lock(_TECHNOBABBLE_TARGET_LOCK, candidate, 1800):
            return int(candidate)
    return 0

def current_aneurysm_profile() -> tuple[float, float]:
    """Return (damage per gained Energy, max adjacent Energy loss) at live Domination."""
    rank = int(current_domination_level())
    damage_per_energy = 3.0
    max_energy_loss = 32.0
    try:
        for attr_name, field_name, values in Skill.GetProgressionData(Aneurysm_ID):
            if str(attr_name).strip().casefold() != "domination magic":
                continue
            table = {int(k): float(v) for k, v in dict(values or {}).items()}
            if not table:
                continue
            value = table.get(rank)
            if value is None:
                nearest = min(table, key=lambda key: abs(int(key) - int(rank)))
                value = table[nearest]
            normalized = str(field_name).replace(" ", "").strip().casefold()
            if normalized in ("damageperenergygain", "damageperenergygained"):
                damage_per_energy = max(0.0, float(value))
            elif normalized in ("maximumenergyloss", "maxenergyloss"):
                max_energy_loss = max(0.0, float(value))
    except Exception:
        pass
    return float(damage_per_energy), float(max_energy_loss)


def aneurysm_target_metrics(agent_id: int) -> dict[str, object]:
    aid = int(agent_id or 0)
    max_energy = float(estimate_enemy_max_energy(aid))
    current_energy = float(estimate_enemy_current_energy(aid))
    missing = max(0.0, max_energy - current_energy)
    damage_per_energy, max_neighbor_loss = current_aneurysm_profile()
    expected_neighbor_loss = max(0.0, min(float(missing), float(max_neighbor_loss)))
    density = 1
    affected: list[int] = []
    try:
        from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
        density = max(1, int(TeamCombatFocus.count_adjacent_enemies(aid, Range.Adjacent.value) or 0))
    except Exception:
        density = 1
    try:
        members = AgentArray.GetEnemyArray()
        center = Agent.GetXY(aid)
        affected = [
            int(eid) for eid in members
            if int(eid) != aid and _valid_enemy(int(eid))
            and math.dist(center, Agent.GetXY(int(eid))) <= float(Range.Adjacent.value)
        ]
    except Exception:
        affected = []
    hp = 1.0
    max_hp = 0.0
    try:
        hp = max(0.0, float(Agent.GetHealth(aid) or 0.0))
        max_hp = max(0.0, float(Agent.GetMaxHealth(aid) or 0.0))
    except Exception:
        pass
    zeroable_neighbors: list[int] = []
    zeroable_energy_total = 0.0
    for enemy_id in affected:
        try:
            projected = float(estimate_enemy_projected_energy(int(enemy_id)) or 0.0)
        except Exception:
            projected = 0.0
        # Famine triggers only when a creature goes from >0 Energy to 0. These
        # are the neighbours the modeled Aneurysm pulse can currently zero.
        if projected > 0.5 and projected <= float(expected_neighbor_loss) + 0.5:
            zeroable_neighbors.append(int(enemy_id))
            zeroable_energy_total += float(projected)

    state = esurge_reservation_state(aid)
    return {
        "target_id": int(aid),
        "max_energy": float(max_energy),
        "current_energy": float(current_energy),
        "missing_energy": float(missing),
        "damage_per_energy": float(damage_per_energy),
        "expected_target_damage": float(missing * damage_per_energy),
        "neighbor_loss_cap": float(max_neighbor_loss),
        "expected_neighbor_loss": float(expected_neighbor_loss),
        "density": int(max(1, density)),
        "affected_neighbors": tuple(int(v) for v in affected),
        "zeroable_neighbors": tuple(int(v) for v in zeroable_neighbors),
        "zeroable_neighbor_count": int(len(zeroable_neighbors)),
        "zeroable_neighbor_energy_total": float(zeroable_energy_total),
        "hp": float(hp),
        "max_hp": float(max_hp),
        "current_hp_abs": float(hp * max_hp) if max_hp > 0.0 else 0.0,
        "pending_esurges": int(state.get("pending_count", 0) or 0),
    }


def active_aneurysm_precast_anchor_ids() -> tuple[int, ...]:
    """Return currently published Stage 1.63 pre-cast Aneurysm anchors."""
    rows = _active_skill_target_lock_events((_ANEURYSM_PRECAST_ANCHOR_LOCK,))
    seen: set[int] = set()
    ordered: list[tuple[int, int]] = []
    for row in rows:
        aid = int(row.get("target_id", 0) or 0)
        if aid <= 0 or aid in seen:
            continue
        seen.add(aid)
        ordered.append((int(row.get("posted_at", 0) or 0), aid))
    ordered.sort(reverse=True)
    return tuple(int(aid) for _posted, aid in ordered)


def active_aneurysm_prewrack_target_ids() -> tuple[int, ...]:
    """Return live Stage 1.64 pre-Wrack neighbours across all setup anchors.

    These targets are temporarily protected from eSurge so the packet cannot
    consume the very Energy state Aneurysm is waiting to collapse to zero.
    """
    seen: set[int] = set()
    result: list[int] = []
    for anchor_id in active_aneurysm_precast_anchor_ids():
        for target_id in aneurysm_prewrack_target_ids(int(anchor_id)):
            tid = int(target_id or 0)
            if tid <= 0 or tid in seen:
                continue
            seen.add(tid)
            result.append(tid)
    return tuple(result)


def _aneurysm_prewrack_candidates(anchor_id: int) -> list[dict[str, object]]:
    """Rank adjacent foes that the planned Aneurysm pulse can zero.

    Candidates are deliberately strict: the foe must currently have some Energy,
    must fit under the expected Aneurysm neighbour drain, and must not be inside a
    protected optional/Fragility conversion pipeline.  Role value is then used to
    prefer healers/support/dangerous casters and dangerous offense.
    """
    aid = int(anchor_id or 0)
    if aid <= 0 or not _valid_enemy(aid):
        return []
    metrics = aneurysm_target_metrics(aid)
    expected_loss = float(metrics.get("expected_neighbor_loss", 0.0) or 0.0)
    if expected_loss <= 0.5:
        return []
    rows: list[tuple[tuple[int, int, int, float, float, int], dict[str, object]]] = []
    for enemy_id in tuple(metrics.get("affected_neighbors", ()) or ()):
        eid = int(enemy_id or 0)
        if eid <= 0 or not _valid_enemy(eid) or not _spellcast_reachable(eid):
            continue
        if is_mind_wrack_active(eid) or _is_locked(_MIND_WRACK_CAST_LOCK, eid):
            continue
        if is_fragility_feeder_window(eid) or is_optional_shatter_pipeline_pending(eid):
            continue
        try:
            hp = max(0.0, float(Agent.GetHealth(eid) or 0.0))
        except Exception:
            hp = 0.0
        if hp <= float(MIND_WRACK_MIN_TARGET_HP):
            continue
        projected = float(estimate_enemy_projected_energy(eid) or 0.0)
        if projected <= 0.5 or projected > expected_loss + 0.5:
            continue
        healer = support = caster = offense = threat = 0
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            scores = EnemyKnowledge.role_scores(eid)
            healer = int(scores.get("healer", 0) or 0)
            support = int(scores.get("support", 0) or 0)
            caster = int(scores.get("caster", 0) or 0)
            offense = int(scores.get("offense", 0) or 0)
            threat = int(EnemyKnowledge.threat_bonus(eid) or 0)
        except Exception:
            pass
        role_value = max(healer, support, caster, offense) + threat
        caster_support = max(healer, support, caster)
        record = {
            "target_id": int(eid),
            "projected_energy": float(projected),
            "expected_neighbor_loss": float(expected_loss),
            "role_value": int(role_value),
            "caster_support": int(caster_support),
            "healer": int(healer),
            "support": int(support),
            "caster": int(caster),
            "offense": int(offense),
            "threat": int(threat),
            "hp": float(hp),
        }
        # Prefer important casters/support first, then expensive-to-zero targets
        # (more energy denied) and healthier foes whose Wrack burst is not waste.
        score = (
            int(role_value), int(caster_support), int(threat),
            float(projected), float(hp), -int(eid),
        )
        rows.append((score, record))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [record for _score, record in rows]


def prepare_aneurysm_precast_setup(anchor_id: int) -> dict[str, object]:
    """Publish one short shared Aneurysm->Mind Wrack pre-cast transaction."""
    aid = int(anchor_id or 0)
    if aid <= 0 or not _valid_enemy(aid):
        return {"anchor_id": 0, "desired_targets": (), "ready_wrack_holders": 0}
    ready_holders = int(_ready_skill_holders_count(int(Mind_Wrack_ID)) or 0)
    candidates = _aneurysm_prewrack_candidates(aid)
    desired_count = min(
        int(ANEURYSM_PREWRACK_MAX_TARGETS), int(ready_holders), len(candidates)
    )
    desired = tuple(int(row["target_id"]) for row in candidates[:desired_count])
    if not desired:
        return {
            "anchor_id": int(aid),
            "desired_targets": (),
            "ready_wrack_holders": int(ready_holders),
            "expected_neighbor_loss": float(aneurysm_target_metrics(aid).get("expected_neighbor_loss", 0.0) or 0.0),
        }

    anchor_posted = bool(_try_lock(_ANEURYSM_PRECAST_ANCHOR_LOCK, aid, ANEURYSM_PRECAST_PLAN_MS))
    posted_targets: list[int] = []
    if anchor_posted:
        for target_id in desired:
            if _try_lock(_ANEURYSM_PREWRACK_TARGET_LOCK, int(target_id), ANEURYSM_PRECAST_PLAN_MS):
                posted_targets.append(int(target_id))
    if anchor_posted and not posted_targets:
        _release_owned_keys((_ANEURYSM_PRECAST_ANCHOR_LOCK,), (aid,))
        anchor_posted = False
    record = {
        "anchor_id": int(aid),
        "desired_targets": tuple(posted_targets),
        "ready_wrack_holders": int(ready_holders),
        "expected_neighbor_loss": float(aneurysm_target_metrics(aid).get("expected_neighbor_loss", 0.0) or 0.0),
        "published": bool(anchor_posted and posted_targets),
    }
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_ANEURYSM_PREWRACK_PLAN",
            target_id=int(aid),
            desired_targets=",".join(str(int(v)) for v in posted_targets),
            desired_count=len(posted_targets),
            ready_wrack_holders=int(ready_holders),
            expected_neighbor_loss=f"{float(record['expected_neighbor_loss']):.1f}",
            policy="stage1_63_zeroable_adjacent_role_priority_precast_setup",
        )
    except Exception:
        pass
    return record


def aneurysm_prewrack_target_ids(anchor_id: int) -> tuple[int, ...]:
    """Read active pre-Wrack target requests belonging to the live anchor."""
    aid = int(anchor_id or 0)
    if aid <= 0 or aid not in set(active_aneurysm_precast_anchor_ids()):
        return ()
    metrics = aneurysm_target_metrics(aid)
    adjacent = set(int(v) for v in tuple(metrics.get("affected_neighbors", ()) or ()))
    rows = _active_skill_target_lock_events((_ANEURYSM_PREWRACK_TARGET_LOCK,))
    ordered: list[tuple[int, int]] = []
    seen: set[int] = set()
    for row in rows:
        target_id = int(row.get("target_id", 0) or 0)
        if target_id <= 0 or target_id in seen or target_id not in adjacent:
            continue
        seen.add(target_id)
        ordered.append((int(row.get("posted_at", 0) or 0), target_id))
    ordered.sort(reverse=True)
    return tuple(int(target_id) for _posted, target_id in ordered)


def aneurysm_precast_status(anchor_id: int) -> dict[str, object]:
    """Return live readiness of the Stage 1.63 pre-cast Wrack transaction."""
    aid = int(anchor_id or 0)
    metrics = aneurysm_target_metrics(aid) if aid > 0 else {}
    expected_loss = float(metrics.get("expected_neighbor_loss", 0.0) or 0.0)
    desired = []
    active = []
    pending = []
    stale = []
    for target_id in aneurysm_prewrack_target_ids(aid):
        target_id = int(target_id)
        if not _valid_enemy(target_id):
            stale.append(target_id)
            continue
        projected = float(estimate_enemy_projected_energy(target_id) or 0.0)
        # If natural regen/movement/model updates made the target no longer
        # zeroable, do not stall the Aneurysm cast waiting for a now-wasted Hex.
        if projected <= 0.5 or projected > expected_loss + 0.5:
            stale.append(target_id)
            continue
        desired.append(target_id)
        # Stage 1.65: accept the same short post-dispatch bridge used by
        # is_mind_wrack_active().  Cross-client lifetime publication can land a
        # controller tick after queue/native completion; treating the bridge as
        # readiness prevents a valid setup from timing out during that gap.
        confirmed_wrack = bool(is_mind_wrack_active(target_id))
        if not confirmed_wrack:
            confirmed_wrack = bool(_hex_lifetime_remaining_ms(target_id, int(Mind_Wrack_ID)) > 0)
        if confirmed_wrack:
            active.append(target_id)
        elif _is_locked(_MIND_WRACK_CAST_LOCK, target_id):
            pending.append(target_id)
    ready = bool(desired) and len(active) == len(desired)
    return {
        "anchor_id": int(aid),
        "desired_targets": tuple(desired),
        "active_targets": tuple(active),
        "pending_targets": tuple(pending),
        "stale_targets": tuple(stale),
        "expected_neighbor_loss": float(expected_loss),
        "ready": bool(ready),
    }


def release_aneurysm_precast_setup(anchor_id: int, *, reason: str = "released") -> None:
    aid = int(anchor_id or 0)
    targets = aneurysm_prewrack_target_ids(aid) if aid > 0 else ()
    if aid > 0:
        _release_owned_keys((_ANEURYSM_PRECAST_ANCHOR_LOCK,), (aid,))
    if targets:
        _release_owned_keys((_ANEURYSM_PREWRACK_TARGET_LOCK,), tuple(int(v) for v in targets))
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_ANEURYSM_PREWRACK_RELEASE",
            target_id=int(aid),
            desired_targets=",".join(str(int(v)) for v in targets),
            reason=str(reason),
            policy="stage1_63_precast_transaction_cleanup",
        )
    except Exception:
        pass


def reserve_specific_aneurysm_target(anchor_id: int) -> bool:
    """Reacquire the short global/target cast lease for a prepared anchor."""
    aid = int(anchor_id or 0)
    if aid <= 0 or not _valid_enemy(aid) or not _spellcast_reachable(aid):
        return False
    if _is_locked(_ANEURYSM_RECENT_PULSE_LOCK, _ANEURYSM_GLOBAL_TARGET):
        return False
    if _is_locked(_ANEURYSM_GLOBAL_CAST_LOCK, _ANEURYSM_GLOBAL_TARGET):
        return False
    if not _try_lock(_ANEURYSM_GLOBAL_CAST_LOCK, _ANEURYSM_GLOBAL_TARGET, ANEURYSM_RESERVATION_MS):
        return False
    if not _try_lock(_ANEURYSM_CAST_LOCK, aid, ANEURYSM_RESERVATION_MS):
        release_aneurysm_reservation(0, reason="prepared_target_reservation_failed")
        return False
    return True


def release_aneurysm_reservation(target_id: int = 0, *, reason: str = "released") -> int:
    """Release this account's pending global/target Aneurysm transaction."""
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        if not email:
            return 0
        target = int(target_id or 0)
        cleared = 0
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccounts()
        for intent in all_accounts.Intents:
            if not getattr(intent, "Active", False):
                continue
            if str(getattr(intent, "OwnerEmail", "") or "") != str(email):
                continue
            if int(getattr(intent, "KindID", 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET):
                continue
            if int(getattr(intent, "IsolationGroupID", 0) or 0) != int(gid):
                continue
            key = int(getattr(intent, "SkillID", 0) or 0)
            tid = int(getattr(intent, "TargetAgentID", 0) or 0)
            is_global = key == int(_ANEURYSM_GLOBAL_CAST_LOCK) and tid == int(_ANEURYSM_GLOBAL_TARGET)
            is_target = key == int(_ANEURYSM_CAST_LOCK) and (target <= 0 or tid == target)
            if not (is_global or is_target):
                continue
            intent.reset()
            cleared += 1
        if cleared > 0:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_ANEURYSM_RESERVATION_RELEASE",
                    target_id=int(target), cleared=int(cleared), reason=str(reason),
                )
            except Exception:
                pass
        return int(cleared)
    except Exception:
        return 0


def invalidate_aneurysm_cast(target_id: int, *, reason: str = "cast_failed") -> int:
    """Retract this account's optimistic Aneurysm facts after observed failure."""
    aid = int(target_id or 0)
    cleared = 0
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        if not email:
            return 0
        for intent in GLOBAL_CACHE.ShMem.GetAllAccounts().Intents:
            if not getattr(intent, "Active", False):
                continue
            if str(getattr(intent, "OwnerEmail", "") or "") != str(email):
                continue
            if int(getattr(intent, "IsolationGroupID", 0) or 0) != int(gid):
                continue
            if int(getattr(intent, "KindID", 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET):
                continue
            key = int(getattr(intent, "SkillID", 0) or 0)
            tid = int(getattr(intent, "TargetAgentID", 0) or 0)
            anchor_fact = tid == aid and key in (int(_ANEURYSM_REFILLED_LOCK), int(_MIND_WRACK_ANCHOR_SETUP_LOCK))
            drain_fact = int(_ANEURYSM_DRAIN_KEY_BASE) < key <= int(_ANEURYSM_DRAIN_KEY_BASE) + int(_ANEURYSM_DRAIN_KEY_MAX)
            if anchor_fact or drain_fact:
                intent.reset()
                cleared += 1
        release_aneurysm_reservation(aid, reason=reason)
        if cleared:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_ANEURYSM_INVALIDATED", target_id=aid, cleared=int(cleared),
                    reason=str(reason), policy="stage1_47_async_failure_retracts_optimistic_anchor",
                )
            except Exception:
                pass
    except Exception:
        pass
    return int(cleared)


def reserve_aneurysm_target() -> int:
    """Reserve a central low-Energy Aneurysm battery for cluster/Famine cycling.

    Stage 2.12 removes the old "consume the current refill anchor first" stall.
    With two Aneurysm copies the team may alternate batteries after a short
    shared pulse gap. While Famine covers the party, target scoring is driven
    first by how many adjacent foes the pulse can actually take from >0 Energy
    to 0, then by a fresh/different anchor, cluster size and refill size.
    """
    _anchor, members = cluster_anchor_and_members()
    if not members:
        return 0
    # Stage 2.44 compatibility: aneurysm_primary_focus
    # 1 if int(aid) == int(primary_focus) else 0
    primary_focus, secondary_focus = _team_damage_focus_pair(consumer_role="aneurysm_two_anchor_pair")
    if _is_locked(_ANEURYSM_RECENT_PULSE_LOCK, _ANEURYSM_GLOBAL_TARGET):
        return 0
    if _is_locked(_ANEURYSM_GLOBAL_CAST_LOCK, _ANEURYSM_GLOBAL_TARGET):
        return 0

    famine_state = famine_active_state()
    famine_covered = bool(
        famine_state.get("active", False) and famine_state.get("covered", False)
    )
    min_missing = float(
        ANEURYSM_FAMINE_MIN_RECENT_DRAIN if famine_covered
        else ANEURYSM_MIN_RECENT_DRAIN
    )

    recent_anchor_ids = set(active_aneurysm_anchor_ids())
    famine_setup_focus_ids = set(active_famine_esurge_focus_ids()) if famine_covered else set()
    rows: list[tuple[tuple[int, int, int, int, int, float, int, int, int, int], int, dict[str, object]]] = []
    for aid in members:
        aid = int(aid)
        if is_mistrust_protected(aid) or _is_locked(_ANEURYSM_CAST_LOCK, aid):
            continue
        metrics = aneurysm_target_metrics(aid)

        # Preserve a live Mind Wrack detonation instead of refilling it early.
        if is_mind_wrack_active(aid) and float(metrics.get("current_energy", 0.0) or 0.0) > 1.0:
            continue
        if int(metrics.get("pending_esurges", 0) or 0) > 0:
            continue

        missing = float(metrics.get("missing_energy", 0.0) or 0.0)
        if missing < float(min_missing):
            continue

        affected_neighbors = tuple(int(v) for v in tuple(metrics.get("affected_neighbors", ()) or ()))
        if not affected_neighbors:
            continue

        hp = float(metrics.get("hp", 0.0) or 0.0)
        current_hp_abs = float(metrics.get("current_hp_abs", 0.0) or 0.0)
        expected_target_damage = float(metrics.get("expected_target_damage", 0.0) or 0.0)
        finisher = bool(
            current_hp_abs > 0.0
            and expected_target_damage + 0.01 >= current_hp_abs
        )

        healer = support = caster = offense = threat = 0
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            scores = EnemyKnowledge.role_scores(aid)
            healer = int(scores.get("healer", 0) or 0)
            support = int(scores.get("support", 0) or 0)
            caster = int(scores.get("caster", 0) or 0)
            offense = int(scores.get("offense", 0) or 0)
            threat = int(EnemyKnowledge.threat_bonus(aid) or 0)
        except Exception:
            pass

        role_value = int(max(healer, support, caster, offense) + threat)
        caster_support = int(max(healer, support, caster))

        # Stage 2.20 kill-before-refill.  Do not rescue a nearly dead target only
        # to manufacture another Energy cycle.  Aneurysm may still be used when
        # its own modeled damage finishes the target.  Healers/supports get a
        # wider safety band because a refill can directly finance recovery.
        if not finisher and hp <= 0.15:
            continue
        if not finisher and max(healer, support) >= 70 and hp <= 0.25:
            continue

        fresh = 1 if aid not in recent_anchor_ids else 0
        density = int(metrics.get("density", 1) or 1)
        zeroable = int(metrics.get("zeroable_neighbor_count", 0) or 0)
        zeroable_energy = float(metrics.get("zeroable_neighbor_energy_total", 0.0) or 0.0)

        prepared_focus = 1 if famine_covered and aid in famine_setup_focus_ids else 0
        score = (
            2 if int(aid) == int(primary_focus) else 1 if int(aid) == int(secondary_focus) else 0,
            int(prepared_focus),
            int(zeroable if famine_covered else 0),
            int(fresh),
            int(len(affected_neighbors)),
            float(round(missing * 10.0)),
            int(round(zeroable_energy * 10.0)) if famine_covered else 0,
            int(density),
            int(role_value),
            int(caster_support),
        )
        metrics["aneurysm_finisher"] = bool(finisher)
        rows.append((score, aid, metrics))

    if not rows:
        return 0

    # Prefer a different battery from the last ~6.5 s whenever one exists.
    fresh_rows = [row for row in rows if int(row[1]) not in recent_anchor_ids]
    if fresh_rows:
        rows = fresh_rows

    rows.sort(key=lambda item: (item[0], -int(item[1])), reverse=True)
    for score, aid, metrics in rows:
        if not _try_lock(_ANEURYSM_GLOBAL_CAST_LOCK, _ANEURYSM_GLOBAL_TARGET, ANEURYSM_RESERVATION_MS):
            return 0
        if not _try_lock(_ANEURYSM_CAST_LOCK, aid, ANEURYSM_RESERVATION_MS):
            release_aneurysm_reservation(0, reason="target_reservation_failed")
            return 0
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_ANEURYSM_RESERVED",
                target_id=int(aid),
                current_energy=f"{float(metrics.get('current_energy', 0.0) or 0.0):.1f}",
                max_energy=f"{float(metrics.get('max_energy', 0.0) or 0.0):.1f}",
                refill_energy=f"{float(metrics.get('missing_energy', 0.0) or 0.0):.1f}",
                density=int(metrics.get("density", 1) or 1),
                affected_neighbors=len(tuple(metrics.get("affected_neighbors", ()) or ())),
                famine_active=bool(famine_covered),
                famine_zeroable_neighbors=int(metrics.get("zeroable_neighbor_count", 0) or 0),
                famine_zeroable_energy=f"{float(metrics.get('zeroable_neighbor_energy_total', 0.0) or 0.0):.1f}",
                min_refill_gate=f"{float(min_missing):.1f}",
                prepared_famine_esurge_focus=bool(int(aid) in famine_setup_focus_ids),
                role_priority=int(score[7]),
                caster_support_priority=int(score[8]),
                finisher=bool(metrics.get("aneurysm_finisher", False)),
                fresh_anchor=bool(int(aid) not in recent_anchor_ids),
                policy="stage2_12_famine_zero_transition_fresh_anchor_cycle_stage2_15_prepared_esurge_focus",
            )
        except Exception:
            pass
        return int(aid)
    return 0


def register_aneurysm_cast(
    target_id: int, expected_gain: float | None = None,
    *, affected_neighbors_override: tuple[int, ...] | None = None,
    prewrack_targets_override: tuple[int, ...] | None = None,
) -> dict[str, object]:
    """Publish bounded refill/drain model estimates after native cast completion.

    Native FINISHED is stronger than queue acceptance but is not direct proof of
    the target-side energy effect.  The resulting locks are therefore bounded
    combat-model inferences, not persistent authoritative game-effect facts.
    """
    aid = int(target_id or 0)
    if aid <= 0:
        return {}
    before = aneurysm_target_metrics(aid)
    refill = float(expected_gain if expected_gain is not None else before.get("missing_energy", 0.0) or 0.0)
    damage_per_energy, max_neighbor_loss = current_aneurysm_profile()
    neighbour_loss = max(0.0, min(float(refill), float(max_neighbor_loss)))
    refill_ok = bool(_try_lock(_ANEURYSM_REFILLED_LOCK, aid, ANEURYSM_REFILLED_MS))
    # Stage 1.63 moves Wrack setup *before* Aneurysm and onto zeroable adjacent
    # foes.  Do not reopen the old post-refill anchor Wrack window: the fresh
    # anchor is now reserved for immediate eSurge consumption.
    wrack_setup_window = False
    ready_wrack_holders = int(_ready_skill_holders_count(int(Mind_Wrack_ID)) or 0)

    affected = tuple(
        int(v) for v in (affected_neighbors_override or ()) if int(v) > 0
    ) or tuple(int(v) for v in before.get("affected_neighbors", ()) if int(v) > 0)
    prewrack_targets = tuple(
        int(v) for v in (prewrack_targets_override or ()) if int(v) > 0
    ) or tuple(int(v) for v in affected if is_mind_wrack_active(int(v)))
    encoded_loss = max(0, min(int(_ANEURYSM_DRAIN_KEY_MAX), int(round(neighbour_loss))))
    drained_posted = 0
    if encoded_loss > 0:
        drain_key = int(_ANEURYSM_DRAIN_KEY_BASE) + int(encoded_loss)
        for enemy_id in affected:
            if _try_lock(drain_key, int(enemy_id), ANEURYSM_NEIGHBOR_DRAIN_MS):
                drained_posted += 1

    release_aneurysm_reservation(aid, reason="cast_registered")
    release_aneurysm_precast_setup(aid, reason="cast_registered")
    pulse_gap_posted = bool(
        _try_lock(_ANEURYSM_RECENT_PULSE_LOCK, _ANEURYSM_GLOBAL_TARGET, ANEURYSM_PULSE_GAP_MS)
    )

    record: dict[str, object] = {
        "target_id": int(aid),
        "refill_energy": float(refill),
        "damage_per_energy": float(damage_per_energy),
        "expected_target_damage": float(refill * damage_per_energy),
        "neighbor_loss_each": float(neighbour_loss),
        "affected_neighbors": tuple(affected),
        "drain_records": int(drained_posted),
        "refill_recorded": bool(refill_ok),
        "mind_wrack_setup_window": bool(wrack_setup_window),
        "prewrack_targets": tuple(prewrack_targets),
        "ready_wrack_holders": int(ready_wrack_holders),
    }
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_ANEURYSM_CAST",
            target_id=int(aid),
            refill_energy=f"{float(refill):.1f}",
            damage_per_energy=f"{float(damage_per_energy):.1f}",
            expected_target_damage=f"{float(refill * damage_per_energy):.1f}",
            neighbor_loss_each=f"{float(neighbour_loss):.1f}",
            affected_neighbors=len(affected),
            drain_records=int(drained_posted),
            mind_wrack_setup_window=bool(wrack_setup_window),
            prewrack_targets=",".join(str(int(v)) for v in prewrack_targets),
            prewrack_count=len(prewrack_targets),
            ready_wrack_holders=int(ready_wrack_holders),
            cast_completion_observed=True,
            effect_confirmed=False,
            model_provisional=True,
            model_refill_ttl_ms=int(ANEURYSM_REFILLED_MS),
            model_neighbor_drain_ttl_ms=int(ANEURYSM_NEIGHBOR_DRAIN_MS),
            pulse_gap_ms=int(ANEURYSM_PULSE_GAP_MS),
            pulse_gap_posted=bool(pulse_gap_posted),
            famine_active=bool(
                famine_active_state().get("active", False)
                and famine_active_state().get("covered", False)
            ),
            famine_zeroable_neighbors=int(before.get("zeroable_neighbor_count", 0) or 0),
            model_basis="native_finished_cast_completion_inference",
            policy="stage2_12_famine_aneurysm_pulse_cycle",
        )
    except Exception:
        pass
    return record


# Locally observed cleanup windows, not kill attribution to a spell/caster.
_CLEANUP_OBSERVED: dict[int, tuple[int, float]] = {}
_CLEANUP_LAST_POLL_MS = 0
_CLEANUP_LAST_SESSION = ""


def _cleanup_track_target(agent_id: int) -> None:
    aid = int(agent_id or 0)
    if aid <= 0:
        return
    try:
        hp = float(Agent.GetHealth(aid) or 0.0)
        now = int(_now_ms() or 0)
    except Exception:
        return
    if 0.0 < hp <= CLEANUP_THRESHOLD and now > 0:
        if len(_CLEANUP_OBSERVED) >= 64:
            # Bounded memory; do not count eviction as kill/death evidence.
            _CLEANUP_OBSERVED.pop(next(iter(_CLEANUP_OBSERVED)))
        _CLEANUP_OBSERVED.setdefault(aid, (now, hp))


def _cleanup_poll_dead_observed() -> None:
    """Record observed death after <=10% HP, never infer death from despawn."""
    global _CLEANUP_LAST_POLL_MS, _CLEANUP_LAST_SESSION
    now = int(_now_ms() or 0)
    if now <= 0 or now - int(_CLEANUP_LAST_POLL_MS) < 200:
        return
    _CLEANUP_LAST_POLL_MS = now
    try:
        from Py4GWCoreLib.Builds.Skills import CombatSession
        session = str(getattr(CombatSession.refresh(), 'key', '') or '')
        if session and _CLEANUP_LAST_SESSION and session != _CLEANUP_LAST_SESSION:
            _CLEANUP_OBSERVED.clear()
        if session:
            _CLEANUP_LAST_SESSION = session
    except Exception:
        pass
    for aid, (first_tick, first_hp) in tuple(_CLEANUP_OBSERVED.items()):
        elapsed = now - int(first_tick)
        if elapsed < 0 or elapsed > 30000:
            _CLEANUP_OBSERVED.pop(aid, None)
            continue
        try:
            dead = bool(Agent.IsValid(aid) and not Agent.IsAlive(aid))
        except Exception:
            dead = False
        if not dead:
            continue
        _CLEANUP_OBSERVED.pop(aid, None)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_CLEANUP_TARGET_DEAD_OBSERVED",
                target_id=int(aid), elapsed_since_first_low_hp_ms=int(elapsed),
                first_observed_hp=round(float(first_hp), 4),
                policy="observed_dead_after_low_hp_not_caster_or_skill_attribution",
            )
        except Exception:
            pass


def _cleanup_candidate_ids(*, origin_id: int = 0) -> list[int]:
    """Low-HP enemies tied to the active packet, plus a fleeing tail.

    The normal source remains the authoritative Adjacent packet.  To solve the
    specific runner case, a <=10% moving foe may remain eligible just outside
    Adjacent while it is still within spellcast range of us and earshot of the
    current team anchor.
    """
    cleanup_poll = globals().get('_cleanup_poll_dead_observed')
    if callable(cleanup_poll):
        cleanup_poll()
    anchor, members = cluster_anchor_and_members()
    # Hero controllers must not use the owning player's position to pick a foe
    # the hero itself cannot cast on. This remains combat-only: no new pulls.
    def reachable(aid: int) -> bool:
        if not _valid_enemy(int(aid)):
            return False
        try:
            xy = Agent.GetXY(int(origin_id)) if int(origin_id or 0) > 0 else Player.GetXY()
            return float(Utils.Distance(xy, Agent.GetXY(int(aid)))) <= float(Range.Spellcast.value)
        except Exception:
            return False

    focus = int(_team_primary_focus_target(consumer_role="cleanup_primary_focus") or 0)
    if focus > 0 and reachable(focus):
        try:
            hp = float(Agent.GetHealth(focus) or 0.0)
        except Exception:
            hp = 0.0
        if 0.0 < hp <= float(CLEANUP_THRESHOLD):
            return [int(focus)]
    candidates = {int(aid) for aid in members if reachable(int(aid))}
    if int(anchor or 0) <= 0:
        return sorted(candidates)
    try:
        player_xy = Agent.GetXY(int(origin_id)) if int(origin_id or 0) > 0 else Player.GetXY()
        anchor_xy = Agent.GetXY(int(anchor))
        for enemy_id in AgentArray.GetEnemyArray() or []:
            aid = int(enemy_id or 0)
            if aid <= 0 or aid in candidates or not _valid_enemy(aid):
                continue
            hp = float(Agent.GetHealth(aid) or 1.0)
            if hp <= 0.0 or hp > CLEANUP_THRESHOLD or not Agent.IsMoving(aid):
                continue
            if float(Utils.Distance(player_xy, Agent.GetXY(aid))) > float(Range.Spellcast.value):
                continue
            if float(Utils.Distance(anchor_xy, Agent.GetXY(aid))) > float(Range.Earshot.value):
                continue
            candidates.add(aid)
    except Exception:
        pass
    return sorted(candidates)


def reserve_energy_surge_cleanup_target(
    preferred_wrack_targets: tuple[int, ...] = (), *,
    origin_id: int = 0, allow_hard_single_target: bool = False,
    hard_only: bool = False,
) -> int:
    """Reserve a <=10% foe for eSurge only when the *packet* still has value.

    Stage 2.42 removes the old blanket rule that made Energy Surge ignore every
    cleanup target.  The elite is allowed to finish a dying primary when its
    nearby splash is still valuable, or when this exact Mesmer owns an active
    Mind Wrack on the target and the non-Hex hit can cash that setup.  One shared
    cleanup lease plus the normal eSurge in-flight slot keeps the decision inside
    the existing cross-account damage model instead of creating a new volley path.
    """
    owned_wrack = {int(v) for v in preferred_wrack_targets if int(v or 0) > 0}
    rows: list[tuple[tuple[int, int, float, float, tuple[float, float, int, int, float, int]], int]] = []
    for aid in _cleanup_candidate_ids(origin_id=origin_id):
        aid = int(aid or 0)
        if aid <= 0:
            continue
        try:
            hp = float(Agent.GetHealth(aid) or 0.0)
        except Exception:
            continue
        if hp <= 0.0 or hp > float(CLEANUP_THRESHOLD):
            continue
        max_claims = 2 if hp <= float(CLEANUP_HARD_THRESHOLD) else 1
        if _live_lock_count(_CLEANUP_TARGET_LOCKS, aid) >= max_claims:
            continue
        if hard_only and hp > float(CLEANUP_HARD_THRESHOLD):
            continue
        # If another eSurge is already in flight to this dying target, do not
        # add another elite. Normal healthy targets may still use the established
        # fan-in rules in reserve_energy_surge_target().
        if int(_live_lock_count(_ESURGE_SLOT_LOCKS, aid)) >= int(ESURGE_CLEANUP_MAX_CONCURRENT):
            continue
        metrics = esurge_target_metrics(aid)
        density = max(1, int(metrics.get("density", 1) or 1))
        expected = float(metrics.get("expected_total_damage", 0.0) or 0.0)
        owned_wrack_active = bool(aid in owned_wrack and is_mind_wrack_active(aid))
        wrack_zero = float(metrics.get("mind_wrack_zero_bonus", 0.0) or 0.0) > 0.0
        packet_value = bool(density >= 2 and expected >= float(ESURGE_MIN_EXPECTED_TOTAL_DAMAGE))
        wrack_value = bool(owned_wrack_active and (wrack_zero or float(metrics.get("effective_drain", 0.0) or 0.0) > 0.0))
        # An actual <=5% enemy must not wait forever just because it has no
        # Adjacent neighbour. Only spend the elite when projected Energy means
        # it will actually cause direct damage; the caller must first offer
        # cheap instant/direct finishers a chance.
        hard_single = bool(
            allow_hard_single_target and hp <= CLEANUP_HARD_THRESHOLD
            and float(metrics.get("effective_drain", 0.0) or 0.0) >= 1.0
            and float(expected) > 0.0
        )
        if not (packet_value or wrack_value or hard_single):
            continue
        score = (
            1 if owned_wrack_active else 0,
            int(density),
            float(expected),
            -float(hp),
            _esurge_score(aid),
        )
        rows.append((score, aid))
    rows.sort(reverse=True)

    for _score, aid in rows:
        hp_at_claim = float(Agent.GetHealth(int(aid)) or 0.0)
        max_claims = 2 if 0 < hp_at_claim <= CLEANUP_HARD_THRESHOLD else 1
        if hp_at_claim <= 0 or hp_at_claim > CLEANUP_THRESHOLD or _live_lock_count(_CLEANUP_TARGET_LOCKS, aid) >= max_claims:
            continue
        cleanup_slot = 0
        for cleanup_lock in _CLEANUP_TARGET_LOCKS:
            if _live_lock_count(_CLEANUP_TARGET_LOCKS, int(aid)) >= max_claims:
                break
            if _live_lock_count((int(cleanup_lock),), int(aid)) > 0:
                continue
            if _try_lock(int(cleanup_lock), int(aid), CLEANUP_TARGET_RESERVATION_MS):
                cleanup_slot = int(cleanup_lock)
                break
        if cleanup_slot <= 0:
            continue
        acquired_slot = 0
        for lock_id in _ESURGE_SLOT_LOCKS:
            if _live_lock_count((int(lock_id),), int(aid)) > 0:
                continue
            if _try_lock(int(lock_id), int(aid), ESURGE_TARGET_RESERVATION_MS):
                acquired_slot = int(lock_id)
                break
        if acquired_slot <= 0:
            _release_owned_keys(_CLEANUP_TARGET_LOCKS, (int(aid),))
            continue
        cleanup_track = globals().get('_cleanup_track_target')
        if callable(cleanup_track):
            cleanup_track(int(aid))
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            metrics = esurge_dispatch_metrics(int(aid))
            CombatDebug.log_event(
                "MESMERWAY_ESURGE_CLEANUP_RESERVATION",
                target_id=int(aid),
                slot_lock_id=int(acquired_slot),
                cleanup_slot_lock_id=int(cleanup_slot),
                target_hp=f"{float(metrics.get('hp', 0.0) or 0.0):.3f}",
                density=int(metrics.get("density", 1) or 1),
                expected_total_damage=f"{float(metrics.get('expected_total_damage', 0.0) or 0.0):.1f}",
                own_mind_wrack=bool(int(aid) in owned_wrack and is_mind_wrack_active(int(aid))),
                hard_single=bool(allow_hard_single_target and hp_at_claim <= CLEANUP_HARD_THRESHOLD
                                 and int(metrics.get("density", 1) or 1) <= 1),
                max_cleanup_claims=int(max_claims),
                policy="stage2_58_shared_one_or_two_cleanup_claim_limit",
            )
        except Exception:
            pass
        return int(aid)
    return 0


def reserve_cleanup_target(*, origin_id: int = 0, hard_only: bool = False) -> int:
    """Claim a quick finisher in the live combat group, in the caster's range.

    One caster at 5-10% and at most two for <=5% avoids three wasted hits on
    the same dying foe; the short renewable TTL permits a backup on a failed
    or incomplete cast without opening another enemy group.
    """
    rows: list[tuple[tuple[int, int, int, int, int, float, int], int]] = []
    for aid in _cleanup_candidate_ids(origin_id=origin_id):
        try:
            hp = float(Agent.GetHealth(aid) or 0.0)
        except Exception:
            continue
        if hp <= 0.0 or hp > CLEANUP_THRESHOLD:
            continue
        hard = 1 if hp <= CLEANUP_HARD_THRESHOLD else 0
        if hard_only and not hard:
            continue
        max_claims = 2 if hard else 1
        if _live_lock_count(_CLEANUP_TARGET_LOCKS, int(aid)) >= max_claims:
            continue
        moving = 0
        casting = 0
        regen = 0
        try:
            moving = 1 if Agent.IsMoving(aid) else 0
        except Exception:
            pass
        try:
            casting = 1 if int(Agent.GetCastingSkillID(aid) or 0) > 0 else 0
        except Exception:
            pass
        try:
            regen = 1 if float(Agent.GetHealthRegen(aid) or 0.0) > 0.0 else 0
        except Exception:
            pass
        threat = 0
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            scores = EnemyKnowledge.role_scores(aid)
            threat = int(scores.get("caster", 0)) + int(EnemyKnowledge.threat_bonus(aid))
        except Exception:
            pass
        score = (hard, moving, casting, regen, threat, -float(hp), -aid)
        rows.append((score, aid))
    rows.sort(reverse=True)
    for _score, aid in rows:
        try:
            hard = float(Agent.GetHealth(int(aid)) or 0.0) <= CLEANUP_HARD_THRESHOLD
        except Exception:
            continue
        max_claims = 2 if hard else 1
        if _live_lock_count(_CLEANUP_TARGET_LOCKS, aid) >= max_claims:
            continue
        for lock_id in _CLEANUP_TARGET_LOCKS:
            if _live_lock_count(_CLEANUP_TARGET_LOCKS, aid) >= max_claims:
                break
            if _live_lock_count((int(lock_id),), int(aid)) > 0:
                continue
            if _try_lock(int(lock_id), int(aid), CLEANUP_TARGET_RESERVATION_MS):
                cleanup_track = globals().get('_cleanup_track_target')
                if callable(cleanup_track):
                    cleanup_track(int(aid))
                return int(aid)
    return 0


def release_cleanup_reservation(agent_id: int, *, reason: str = "") -> None:
    aid = int(agent_id or 0)
    if aid <= 0:
        return
    _release_owned_keys(_CLEANUP_TARGET_LOCKS, (aid,))
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "MESMERWAY_CLEANUP_RESERVATION_RELEASE", target_id=aid, reason=str(reason or "released"),
            policy="stage1_57_failed_cleanup_does_not_hold_finisher_slot",
        )
    except Exception:
        pass


def cleanup_target_info(agent_id: int) -> dict[str, object]:
    aid = int(agent_id or 0)
    hp = 1.0
    moving = False
    casting_skill = 0
    try:
        hp = float(Agent.GetHealth(aid) or 0.0)
    except Exception:
        pass
    try:
        moving = bool(Agent.IsMoving(aid))
    except Exception:
        pass
    try:
        casting_skill = int(Agent.GetCastingSkillID(aid) or 0)
    except Exception:
        pass
    return {
        "hp": float(hp),
        "hard": bool(hp <= CLEANUP_HARD_THRESHOLD),
        "moving": bool(moving),
        "casting_skill_id": int(casting_skill),
    }


def signet_of_corruption_target_metrics(agent_id: int) -> dict[str, int]:
    """Nearby packet value for Signet of Corruption.

    The signet neither spends nor removes enemy Energy, so it is a safe late
    filler for the eSurge/Aneurysm battery model.  Value comes from hitting a
    real Nearby packet and from enemies already hexed/conditioned by the team.
    """
    aid = int(agent_id or 0)
    if aid <= 0 or not _valid_enemy(aid):
        return {"affected": 0, "qualified": 0}
    _anchor, members = cluster_anchor_and_members()
    if not members:
        return {"affected": 0, "qualified": 0}
    try:
        center = Agent.GetXY(aid)
    except Exception:
        return {"affected": 0, "qualified": 0}
    affected = 0
    qualified = 0
    for enemy_id in members:
        eid = int(enemy_id or 0)
        if eid <= 0 or not _valid_enemy(eid):
            continue
        try:
            if Utils.Distance(center, Agent.GetXY(eid)) > float(Range.Nearby.value):
                continue
        except Exception:
            continue
        affected += 1
        try:
            if Agent.IsHexed(eid) or Agent.IsConditioned(eid):
                qualified += 1
        except Exception:
            pass
    return {"affected": int(affected), "qualified": int(qualified)}


def reserve_signet_of_corruption_target(min_qualified: int = 2) -> int:
    """Reserve a dense, already-prepared Nearby packet for Signet of Corruption.

    One or zero prepared foes is intentionally skipped; the slot should not steal
    casting time from the Mesmerway core for essentially single-target filler.
    """
    _anchor, members = cluster_anchor_and_members()
    if not members:
        return 0
    rows: list[tuple[int, int, tuple[float, int, int, float, int], int]] = []
    threshold = max(1, int(min_qualified))
    for aid in members:
        aid = int(aid)
        if not _valid_enemy(aid) or _is_locked(_SIGNET_CORRUPTION_TARGET_LOCK, aid):
            continue
        metrics = signet_of_corruption_target_metrics(aid)
        qualified = int(metrics.get("qualified", 0) or 0)
        affected = int(metrics.get("affected", 0) or 0)
        if qualified < threshold or affected < 2:
            continue
        rows.append((qualified, affected, _enemy_score(aid), aid))
    rows.sort(reverse=True)
    for _qualified, _affected, _score, aid in rows:
        if _try_lock(_SIGNET_CORRUPTION_TARGET_LOCK, int(aid), SIGNET_CORRUPTION_TARGET_RESERVATION_MS):
            return int(aid)
    return 0


def reserve_evas_target() -> int:
    _anchor, members = cluster_anchor_and_members()
    rows: list[tuple[int, tuple[float, int, int, float, int], int]] = []
    for aid in members:
        if is_mistrust_protected(aid) or _is_locked(_EVAS_TARGET_LOCK, aid):
            continue
        try:
            hp = float(Agent.GetHealth(aid) or 0.0)
        except Exception:
            hp = 1.0
        if hp <= 0.30:
            continue
        setup = 0
        try:
            if Agent.IsHexed(aid) or Agent.IsConditioned(aid):
                setup = 1
        except Exception:
            pass
        rows.append((setup, _enemy_score(aid), int(aid)))
    rows.sort(reverse=True)
    for _setup, _score, aid in rows:
        if _try_lock(_EVAS_TARGET_LOCK, aid, EVAS_TARGET_RESERVATION_MS):
            return int(aid)
    return 0


def reserve_unnatural_target(preferred_wrack_targets: tuple[int, ...] = ()) -> int:
    """Reserve Unnatural for packet damage, with owned Mind Wrack first.

    Unnatural is not merely a finisher: on a hexed/enchanted primary its Nearby
    packet damage is excellent.  Stage 2.42 therefore ranks the caster's own
    active Mind-Wrack targets first, then prepared dense targets, instead of
    falling back to the generic enemy score alone.
    """
    _anchor, members = cluster_anchor_and_members()
    if not members:
        return 0
    # Stage 2.44 compatibility: unnatural_primary_focus / return int(primary_focus)
    primary_focus, secondary_focus = _team_damage_focus_pair(consumer_role="unnatural_two_anchor_pair")
    owned_wrack = {int(v) for v in preferred_wrack_targets if int(v or 0) > 0}
    for wrack_target in (int(primary_focus), int(secondary_focus)):
        if wrack_target > 0 and wrack_target in owned_wrack and is_mind_wrack_active(wrack_target) and wrack_target in set(int(v) for v in members):
            return int(wrack_target)
    assigned, _lane, _count = _assigned_damage_anchor(primary_focus, secondary_focus)
    if assigned > 0 and assigned in set(int(v) for v in members) and _valid_enemy(assigned):
        return int(assigned)
    rows: list[tuple[tuple[int, int, int, tuple[float, float, int, int, float, int]], int]] = []
    for aid in members:
        aid = int(aid)
        if _is_locked(_UNNATURAL_TARGET_LOCK, aid):
            continue
        prepared = 0
        try:
            prepared = 1 if (Agent.IsHexed(aid) or Agent.IsEnchanted(aid)) else 0
        except Exception:
            prepared = 0
        own_wrack = 1 if (aid in owned_wrack and is_mind_wrack_active(aid)) else 0
        try:
            density = max(1, int(esurge_target_metrics(aid).get("density", 1) or 1))
        except Exception:
            density = 1
        rows.append(((own_wrack, prepared, density, _enemy_score(aid)), aid))
    rows.sort(reverse=True)
    for _score, aid in rows:
        if _try_lock(_UNNATURAL_TARGET_LOCK, int(aid), FILLER_TARGET_RESERVATION_MS):
            return int(aid)
    return 0


def reserve_spiritual_pain_target(preferred_wrack_targets: tuple[int, ...] = ()) -> int:
    """Reserve Spiritual Pain as coordinated normal damage, not finisher-only.

    The caster's own active Mind-Wrack target wins because Spiritual Pain is a
    non-Hex Mesmer skill and therefore also advances that paid energy-pressure
    setup.  Otherwise retain the established threat/energy target score.
    """
    _anchor, members = cluster_anchor_and_members()
    if not members:
        return 0
    # Stage 2.44 compatibility: spiritual_primary_focus / return int(primary_focus)
    primary_focus, secondary_focus = _team_damage_focus_pair(consumer_role="spiritual_two_anchor_pair")
    owned_wrack = {int(v) for v in preferred_wrack_targets if int(v or 0) > 0}
    for wrack_target in (int(primary_focus), int(secondary_focus)):
        if wrack_target > 0 and wrack_target in owned_wrack and is_mind_wrack_active(wrack_target) and wrack_target in set(int(v) for v in members):
            return int(wrack_target)
    assigned, _lane, _count = _assigned_damage_anchor(primary_focus, secondary_focus)
    if assigned > 0 and assigned in set(int(v) for v in members) and _valid_enemy(assigned):
        return int(assigned)
    rows: list[tuple[tuple[int, tuple[float, float, int, int, float, int]], int]] = []
    for aid in members:
        aid = int(aid)
        if _is_locked(_SPIRITUAL_PAIN_TARGET_LOCK, aid):
            continue
        own_wrack = 1 if (aid in owned_wrack and is_mind_wrack_active(aid)) else 0
        rows.append(((own_wrack, _enemy_score(aid)), aid))
    rows.sort(reverse=True)
    for _score, aid in rows:
        if _try_lock(_SPIRITUAL_PAIN_TARGET_LOCK, int(aid), FILLER_TARGET_RESERVATION_MS):
            return int(aid)
    return 0
