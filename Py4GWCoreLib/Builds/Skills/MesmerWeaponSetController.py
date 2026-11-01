"""Adaptive weapon-set controller for the HR Mesmerway project player accounts.

Expected layout:
- F1 / weapon set 1: 40/40 burst set (wand/focus; non-staff)
- F2 / weapon set 2: SR5 sustain staff

Policy:
- only switch INTO SR5 while actually in combat and Energy <= 35%;
- switch back to 40/40 at Energy >= 75%;
- never START a switch while casting/knocked down/dead;
- 3 s hold after a confirmed swap plus retry backoff prevents F1/F2 thrash;
- confirmation prefers the actual equipped weapon type and also observes the
  native WeaponSetSwapComplete/Cancel UI messages when available;
- a failed/misconfigured F2 staff does not cause key-spam;
- Stage 1.14 verifies swaps with the equipped item signature returned by
  ``Agent.GetWeaponExtraData``.  F1's wand+focus signature is learned before the
  first F2 request; F2's staff signature is learned only after the equipped
  item IDs actually change.  A swallowed keypress is retried once, and an F1
  swap is not called confirmed until the original 40/40 item IDs are back.

Implementation note (Stage 1.14): HeroAI rebuilds the build coroutine often enough
that a multi-frame ``yield from ActivateWeaponSet`` cannot safely own the whole
swap transaction.  This controller therefore uses a persistent, non-blocking
state machine and sends the weapon-set input as one atomic ``UIManager.Keypress``.
The following HeroAI ticks are used only for confirmation/failure; no key is held
across ticks and no weapon-swap state lives only on the Python generator stack.

Stage 1.27 adds a map-entry invariant: every newly loaded explorable instance is
normalized to F1 before the ordinary Energy hysteresis resumes. This fixes the
case where Guild Wars carries F2 across a zone transition while GetWeaponType()
stale-reports a Scepter. The stronger build contract (F1 has an offhand; F2 is
two-handed/no-offhand) is used at map entry, and a bounded F1 keypress transaction
forces the baseline when F2/unknown is observed.

The controller does not inspect item mods themselves.  The setup contract remains
simple and auditable: F1 must be the wand+focus 40/40 set, F2 the SR5 staff.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

from Py4GWCoreLib import Agent, Map, Player, UIManager
from Py4GWCoreLib.enums_src.UI_enums import UIMessage, ControlAction


BURST_SET = 1
SUSTAIN_SET = 2
LOW_ENERGY_THRESHOLD = 0.35
HIGH_ENERGY_THRESHOLD = 0.75
MIN_CONFIRMED_HOLD_MS = 3000
FAILED_RETRY_MS = 1800
REPEATED_FAILURE_RETRY_MS = 5000
CONFIRM_TIMEOUT_MS = 5500
KEYPRESS_RETRY_AFTER_MS = 1400
MAX_KEYPRESS_ATTEMPTS = 2
STATE_HEARTBEAT_MS = 15000
MAP_ENTRY_STABILIZE_MS = 1200
MAP_REENTRY_UPTIME_DROP_MS = 1500

# GameData_enums.Weapon Staff1/Staff/Staff2/Staff3 are 11..14. Keeping this
# local avoids making the combat path depend on enum import shape changes.
_STAFF_WEAPON_TYPES = frozenset((11, 12, 13, 14))


@dataclass(frozen=True, slots=True)
class _EquipmentSnapshot:
    weapon_type: int = 0
    weapon_name: str = "Unknown"
    weapon_item_id: int = 0
    weapon_item_type_raw: int = 0
    offhand_item_id: int = 0
    offhand_item_type_raw: int = 0

    @property
    def signature(self) -> tuple[int, int, int, int]:
        return (
            int(self.weapon_item_id), int(self.weapon_item_type_raw),
            int(self.offhand_item_id), int(self.offhand_item_type_raw),
        )


@dataclass(slots=True)
class _State:
    hold_until_ms: int = 0
    retry_after_ms: int = 0
    last_requested_set: int = 0
    last_confirmed_set: int = 0
    failure_count: int = 0

    # Stage 1.13 logical weapon-set memory.  The live Agent weapon-type query can
    # lag or remain on the old Scepter after a real set swap, so the controller
    # must not depend on that query alone for the return F2 -> F1 decision.
    logical_set: int = 0
    logical_set_source: str = ""

    # Stage 1.14 exact equipped-item fingerprints.  Tuple layout follows
    # Agent.GetWeaponExtraData(): weapon_item_id, weapon_item_type,
    # offhand_item_id, offhand_item_type.
    burst_signature: tuple[int, int, int, int] = (0, 0, 0, 0)
    sustain_signature: tuple[int, int, int, int] = (0, 0, 0, 0)

    active_logged: bool = False
    last_state_log_ms: int = 0

    # Persistent transaction state. Never rely on a generator surviving a tick.
    pending_set: int = 0
    pending_action: int = 0
    pending_phase: str = ""  # "confirm" while a Keypress transaction is pending
    request_started_ms: int = 0
    phase_started_ms: int = 0
    ui_log_start_index: int = 0
    request_energy_pct: float = 0.0
    request_in_combat: bool = False
    pending_previous_logical_set: int = 0
    pending_pre_signature: tuple[int, int, int, int] = (0, 0, 0, 0)
    keypress_attempts: int = 0
    last_keypress_ms: int = 0
    pending_reason: str = ""

    # Stage 1.27 map-entry normalization. Weapon sets persist across map changes in
    # Guild Wars, while the controller state/signature knowledge may be rebuilt.
    # Every new explorable instance therefore starts from a known F1 baseline
    # before the normal 35% -> F2 / 75% -> F1 hysteresis is allowed to operate.
    last_map_id: int = 0
    last_instance_uptime_ms: int = 0
    map_entry_detected_ms: int = 0
    map_entry_generation: int = 0
    map_f1_normalized: bool = False
    map_f1_normalization_failed: bool = False


class MesmerWeaponSetController:
    def __init__(self, role: str = "mesmer") -> None:
        self.role = str(role or "mesmer")
        self.state = _State()

    @staticmethod
    def _now_ms() -> int:
        try:
            import PySystem
            value = int(PySystem.get_tick_count64() or 0)
            if value > 0:
                return value
        except Exception:
            pass
        # Fallback is important for deterministic progress if PySystem is briefly
        # unavailable during map/loading transitions.
        return int(time.monotonic() * 1000.0)

    @staticmethod
    def _map_snapshot() -> tuple[int, int, bool]:
        """Return (map_id, instance_uptime_ms, ready_explorable)."""
        try:
            map_id = int(Map.GetMapID() or 0)
            uptime = int(Map.GetInstanceUptime() or 0)
            ready = bool(Map.IsMapReady()) and not bool(Map.IsMapLoading()) and bool(Map.IsExplorable())
            return map_id, max(0, uptime), bool(ready and map_id > 0)
        except Exception:
            return 0, 0, False

    def _observe_map_entry(self) -> bool:
        """Detect a fresh explorable instance, including same-map re-entry.

        Returns True only on the tick on which a new instance is recognized.
        A controller reconstructed after zoning also treats its first valid
        explorable snapshot as a new instance, which is exactly what we want.
        """
        map_id, uptime, ready = self._map_snapshot()
        if not ready:
            return False

        previous_map = int(self.state.last_map_id or 0)
        previous_uptime = int(self.state.last_instance_uptime_ms or 0)
        new_instance = False
        reason = ""
        if previous_map <= 0:
            new_instance = True
            reason = "first_ready_explorable"
        elif map_id != previous_map:
            new_instance = True
            reason = "map_id_changed"
        elif previous_uptime > 0 and uptime + MAP_REENTRY_UPTIME_DROP_MS < previous_uptime:
            new_instance = True
            reason = "instance_uptime_reset"

        self.state.last_map_id = int(map_id)
        self.state.last_instance_uptime_ms = int(uptime)
        if not new_instance:
            return False

        # Any transaction/hold/backoff inherited from the previous instance is
        # stale. Stage 1.68 also treats the exact equipped-item fingerprints as
        # session/instance-local evidence. In the Apo runtime the native item IDs
        # can be rebased after a client/session restart even though the physical
        # F1/F2 equipment is unchanged. Carrying the previous exact IDs into the
        # next instance makes a real F1 swap look like a failed transaction.
        #
        # Clear both fingerprints and relearn them from the normal map-entry
        # contract (F1 = wand+focus/offhand present, F2 = no-offhand staff). This
        # is safe on ordinary zoning as well: the contract is re-observed before
        # energy hysteresis resumes, while no speculative set is marked confirmed.
        previous_burst_signature = self.state.burst_signature
        previous_sustain_signature = self.state.sustain_signature
        self._clear_pending()
        self.state.burst_signature = (0, 0, 0, 0)
        self.state.sustain_signature = (0, 0, 0, 0)
        self.state.hold_until_ms = 0
        self.state.retry_after_ms = 0
        self.state.failure_count = 0
        self.state.last_requested_set = 0
        self.state.last_confirmed_set = 0
        self.state.logical_set = 0
        self.state.logical_set_source = "map_entry_reprobe"
        self.state.map_entry_detected_ms = self._now_ms()
        self.state.map_entry_generation += 1
        self.state.map_f1_normalized = False
        self.state.map_f1_normalization_failed = False
        self.state.last_state_log_ms = 0

        signature = self._weapon_signature()
        weapon_type, weapon_name = self._weapon_type()
        if self._signature_valid(previous_burst_signature) or self._signature_valid(previous_sustain_signature):
            self._log(
                "MESMER_WEAPON_SIGNATURES_RESET",
                map_id=int(map_id),
                generation=int(self.state.map_entry_generation),
                reason="new_instance_relearn_exact_item_ids",
                **self._signature_fields(previous_burst_signature, "previous_burst"),
                **self._signature_fields(previous_sustain_signature, "previous_sustain"),
                policy="stage1_68_session_local_weapon_item_signatures",
            )
        self._log(
            "MESMER_WEAPON_MAP_ENTRY",
            map_id=int(map_id),
            instance_uptime_ms=int(uptime),
            generation=int(self.state.map_entry_generation),
            reason=str(reason),
            weapon_type=int(weapon_type),
            weapon_name=str(weapon_name),
            actual_staff=bool(self._is_staff()),
            **self._signature_fields(signature, "current"),
            **self._signature_fields(self.state.burst_signature, "burst"),
            **self._signature_fields(self.state.sustain_signature, "sustain"),
        )
        return True

    def _mark_map_f1_normalized(self, *, source: str, signature: tuple[int, int, int, int] | None = None) -> None:
        if self.state.map_f1_normalized:
            return
        sig = signature if signature is not None else self._weapon_signature()
        self.state.map_f1_normalized = True
        self.state.map_f1_normalization_failed = False
        self.state.logical_set = BURST_SET
        self.state.logical_set_source = str(source or "map_entry_f1_normalized")
        self._log(
            "MESMER_WEAPON_MAP_F1_READY",
            map_id=int(self.state.last_map_id or 0),
            generation=int(self.state.map_entry_generation or 0),
            source=str(source or ""),
            **self._signature_fields(sig, "current"),
            **self._signature_fields(self.state.burst_signature, "burst"),
            **self._signature_fields(self.state.sustain_signature, "sustain"),
        )

    def _map_entry_normalization_step(self, energy_pct: float, in_combat: bool) -> bool:
        """Establish F1 once per new explorable instance.

        The F1/F2 contract is stronger than flaky GetWeaponType telemetry:
        F1 is wand+focus (offhand present), F2 is the two-handed SR5 staff
        (no offhand). This specifically fixes zoning into a new map while F2 is
        still equipped and the native weapon-type API stale-reports Scepter.
        """
        if self.state.map_f1_normalized:
            return False
        if int(self.state.last_map_id or 0) <= 0:
            return False

        # Let map/player item telemetry settle briefly after zoning.
        now = self._now_ms()
        if now - int(self.state.map_entry_detected_ms or now) < MAP_ENTRY_STABILIZE_MS:
            return False

        # A normalization transaction already owns the weapon slot.
        if int(self.state.pending_set or 0) > 0:
            return self._process_pending()

        signature = self._weapon_signature()
        signature_valid = self._signature_valid(signature)

        # Contract-positive wand+focus means we are already on F1. Learn/relearn
        # the exact F1 fingerprint and continue without sending a redundant key.
        if signature_valid and self._signature_has_offhand(signature):
            self._learn_signature(BURST_SET, signature, source="map_entry_wand_focus")
            self._mark_map_f1_normalized(source="map_entry_observed_f1", signature=signature)
            return False

        # A valid no-offhand signature is the important Stage 1.27 case. Under
        # this build contract it is F2 even when GetWeaponType incorrectly says
        # Scepter. Learn it as sustain, then force F1 independent of Energy.
        if signature_valid and not self._signature_has_offhand(signature):
            self._learn_signature(SUSTAIN_SET, signature, source="map_entry_no_offhand_f2")
            self.state.logical_set = SUSTAIN_SET
            self.state.logical_set_source = "map_entry_no_offhand_f2"

        # Avoid spending several confirmation ticks on a map-entry swap if the
        # client spawned directly into active combat. In that rare case combat
        # continues on the carried set and F1 normalization is retried as soon as
        # combat ends. Normal dungeon zoning is out-of-combat and normalizes
        # immediately.
        if bool(in_combat):
            return False

        # If item telemetry is absent but native weapon type positively says staff,
        # we still know F2 is active. If both are unavailable/ambiguous, pressing
        # F1 once is the safest idempotent normalization after the settle window.
        if not self._safe_to_start_swap():
            return False
        return self._start_swap(
            BURST_SET,
            float(energy_pct),
            bool(in_combat),
            reason="map_entry_force_f1",
        )

    @staticmethod
    def _equipment_snapshot() -> _EquipmentSnapshot:
        """Read logical weapon type and item IDs from one LivingAgent snapshot."""
        try:
            player_id = int(Player.GetAgentID() or 0)
            if player_id <= 0:
                return _EquipmentSnapshot()
            living = Agent.GetLivingAgentByID(player_id)
            if living is None:
                return _EquipmentSnapshot()
            weapon_type = int(getattr(living, "weapon_type", 0) or 0)
            try:
                from Py4GWCoreLib.enums_src.GameData_enums import Weapon, Weapon_Names
                try:
                    weapon_name = str(Weapon_Names.get(Weapon(weapon_type), "Unknown") or "Unknown")
                except ValueError:
                    weapon_name = "Unknown"
            except Exception:
                weapon_name = "Unknown"
            return _EquipmentSnapshot(
                weapon_type=weapon_type, weapon_name=weapon_name,
                weapon_item_id=int(getattr(living, "weapon_item_id", 0) or 0),
                weapon_item_type_raw=int(getattr(living, "weapon_item_type", 0) or 0),
                offhand_item_id=int(getattr(living, "offhand_item_id", 0) or 0),
                offhand_item_type_raw=int(getattr(living, "offhand_item_type", 0) or 0),
            )
        except Exception:
            return _EquipmentSnapshot()

    @classmethod
    def _weapon_type(cls) -> tuple[int, str]:
        snapshot = cls._equipment_snapshot()
        return int(snapshot.weapon_type), str(snapshot.weapon_name)

    @classmethod
    def _weapon_signature(cls) -> tuple[int, int, int, int]:
        return cls._equipment_snapshot().signature

    @staticmethod
    def _snapshot_is_staff(snapshot: _EquipmentSnapshot) -> bool:
        return bool(
            int(snapshot.weapon_type) in _STAFF_WEAPON_TYPES
            or "staff" in str(snapshot.weapon_name or "").casefold()
        )

    @staticmethod
    def _signature_valid(signature: tuple[int, int, int, int]) -> bool:
        try:
            return int(signature[0] or 0) > 0 or int(signature[2] or 0) > 0
        except Exception:
            return False

    @staticmethod
    def _signature_has_offhand(signature: tuple[int, int, int, int]) -> bool:
        try:
            return int(signature[2] or 0) > 0
        except Exception:
            return False

    @staticmethod
    def _signature_fields(signature: tuple[int, int, int, int], prefix: str) -> dict:
        try:
            weapon_id, weapon_type, offhand_id, offhand_type = signature
        except Exception:
            weapon_id = weapon_type = offhand_id = offhand_type = 0
        return {
            f"{prefix}_weapon_item_id": int(weapon_id or 0),
            # These are raw native item-class bytes, NOT GameData_enums.Weapon.
            f"{prefix}_weapon_item_type": int(weapon_type or 0),
            f"{prefix}_weapon_item_type_raw": int(weapon_type or 0),
            f"{prefix}_offhand_item_id": int(offhand_id or 0),
            f"{prefix}_offhand_item_type": int(offhand_type or 0),
            f"{prefix}_offhand_item_type_raw": int(offhand_type or 0),
        }

    @classmethod
    def _is_staff(cls) -> bool:
        return cls._snapshot_is_staff(cls._equipment_snapshot())

    @staticmethod
    def _safe_to_start_swap() -> bool:
        try:
            player_id = int(Player.GetAgentID() or 0)
            if player_id <= 0 or not Agent.IsValid(player_id) or not Agent.IsAlive(player_id):
                return False
            if Agent.IsCasting(player_id):
                return False
            try:
                if Agent.IsKnockedDown(player_id):
                    return False
            except Exception:
                pass
            return True
        except Exception:
            return False

    @staticmethod
    def _ui_log_len() -> int:
        try:
            return len(UIManager.GetUIMessageLogs() or [])
        except Exception:
            return 0

    @staticmethod
    def _new_swap_event(start_index: int) -> str:
        """Best-effort native confirmation without clearing the global UI log."""
        try:
            logs = list(UIManager.GetUIMessageLogs() or [])
            if start_index < 0:
                start_index = 0
            rows = logs[start_index:] if start_index <= len(logs) else logs[-32:]
            complete = int(UIMessage.kWeaponSetSwapComplete)
            cancel = int(UIMessage.kWeaponSetSwapCancel)
            for row in rows:
                try:
                    msg_id = int(row[1])
                    incoming = bool(row[2])
                except Exception:
                    continue
                if not incoming:
                    continue
                if msg_id == cancel:
                    return "cancel"
                if msg_id == complete:
                    return "complete"
        except Exception:
            pass
        return ""

    def _log(self, event: str, **fields) -> None:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(str(event), role=self.role, **fields)
        except Exception:
            pass

    @staticmethod
    def _action_for_set(set_number: int) -> int:
        return int(ControlAction.ControlAction_ActivateWeaponSet1.value) + (int(set_number) - 1)

    def _effective_current_set(self) -> int:
        """Return the best current-set estimate, preferring exact item IDs."""
        signature = self._weapon_signature()
        if self._signature_valid(signature):
            if self._signature_valid(self.state.burst_signature) and signature == self.state.burst_signature:
                return BURST_SET
            if self._signature_valid(self.state.sustain_signature) and signature == self.state.sustain_signature:
                return SUSTAIN_SET

        # Positive staff detection is still useful as a fallback while the F2
        # signature has not yet been learned.  A negative result is never allowed
        # to erase logical F2 memory because GetWeaponType was stale in live logs.
        if self._is_staff():
            return SUSTAIN_SET
        logical = int(self.state.logical_set or 0)
        if logical in (BURST_SET, SUSTAIN_SET):
            return logical
        return BURST_SET

    def _desired_set(self, energy_pct: float, in_combat: bool) -> int:
        energy = max(0.0, min(1.0, float(energy_pct)))
        current_set = self._effective_current_set()

        # SR5 only pays while kills can happen. Do not move a low-energy Mesmer
        # into the staff simply because it is standing out of combat.
        if bool(in_combat) and energy <= LOW_ENERGY_THRESHOLD and current_set != SUSTAIN_SET:
            return SUSTAIN_SET

        # Stage 1.13: returning to burst uses logical-set memory as well as the
        # live weapon type.  Therefore a successful/assumed F2 press can always
        # produce an F1 request again at >=75%, even if weapon-type telemetry is
        # stale and still says "Scepter".
        if energy >= HIGH_ENERGY_THRESHOLD and current_set == SUSTAIN_SET:
            return BURST_SET

        return 0

    def _clear_pending(self) -> None:
        self.state.pending_set = 0
        self.state.pending_action = 0
        self.state.pending_phase = ""
        self.state.request_started_ms = 0
        self.state.phase_started_ms = 0
        self.state.ui_log_start_index = 0
        self.state.request_energy_pct = 0.0
        self.state.request_in_combat = False
        self.state.pending_previous_logical_set = 0
        self.state.pending_pre_signature = (0, 0, 0, 0)
        self.state.keypress_attempts = 0
        self.state.last_keypress_ms = 0
        self.state.pending_reason = ""

    def _fail_pending(self, reason: str, *, error_type: str = "") -> bool:
        desired = int(self.state.pending_set or self.state.last_requested_set or 0)
        pending_reason = str(self.state.pending_reason or "")
        previous_logical = int(self.state.pending_previous_logical_set or 0)
        self.state.failure_count += 1
        now = self._now_ms()
        backoff = FAILED_RETRY_MS if self.state.failure_count < 3 else REPEATED_FAILURE_RETRY_MS
        self.state.retry_after_ms = now + backoff
        if previous_logical in (BURST_SET, SUSTAIN_SET):
            self.state.logical_set = previous_logical
            self.state.logical_set_source = "swap_failed_reverted"
        post_snapshot = self._equipment_snapshot()
        post_type, post_name = int(post_snapshot.weapon_type), str(post_snapshot.weapon_name)
        post_signature = post_snapshot.signature
        self._log(
            "MESMER_WEAPON_SWAP_FAILED",
            requested_set=desired,
            energy_pct=f"{float(self.state.request_energy_pct):.3f}",
            reason=str(reason),
            error_type=str(error_type or ""),
            expected_staff=bool(desired == SUSTAIN_SET),
            post_weapon_type=post_type,
            post_weapon_name=post_name,
            weapon_type_source="LivingAgent.weapon_type",
            item_type_source="LivingAgent.weapon_item_type_raw_not_Weapon_enum",
            telemetry_divergent=bool(
                self._signature_valid(post_signature)
                and not self._signature_has_offhand(post_signature)
                and int(post_type) not in _STAFF_WEAPON_TYPES
            ),
            logical_set=int(self.state.logical_set or 0),
            logical_source=str(self.state.logical_set_source or ""),
            retry_backoff_ms=backoff,
            failure_count=self.state.failure_count,
            keypress_attempts=int(self.state.keypress_attempts or 0),
            request_reason=pending_reason,
            **self._signature_fields(post_signature, "post"),
            **self._signature_fields(self.state.burst_signature, "burst"),
            **self._signature_fields(self.state.sustain_signature, "sustain"),
        )
        if pending_reason == "map_entry_force_f1":
            # Fail open after the existing bounded transaction (two keypresses).
            # Combat must not be held hostage by a misconfigured/missing F1 set.
            self.state.map_f1_normalized = True
            self.state.map_f1_normalization_failed = True
            self._log(
                "MESMER_WEAPON_MAP_F1_FAILED",
                map_id=int(self.state.last_map_id or 0),
                generation=int(self.state.map_entry_generation or 0),
                reason=str(reason),
                **self._signature_fields(post_signature, "current"),
            )
        self._clear_pending()
        return True

    def _learn_signature(self, set_number: int, signature: tuple[int, int, int, int], *, source: str) -> None:
        if not self._signature_valid(signature):
            return
        if int(set_number) == BURST_SET:
            if signature == self.state.burst_signature:
                return
            self.state.burst_signature = signature
        elif int(set_number) == SUSTAIN_SET:
            if signature == self.state.sustain_signature:
                return
            self.state.sustain_signature = signature
        else:
            return
        self._log(
            "MESMER_WEAPON_SIGNATURE_LEARNED",
            learned_set=int(set_number),
            source=str(source or ""),
            **self._signature_fields(signature, "learned"),
        )

    def _confirm_pending(
        self, *, confirmation_source: str,
        signature: tuple[int, int, int, int] | None = None,
        snapshot: _EquipmentSnapshot | None = None,
    ) -> bool:
        desired = int(self.state.pending_set or 0)
        pending_reason = str(self.state.pending_reason or "")
        post_snapshot = snapshot if snapshot is not None else self._equipment_snapshot()
        post_type, post_name = int(post_snapshot.weapon_type), str(post_snapshot.weapon_name)
        post_signature = signature if signature is not None else post_snapshot.signature
        now = self._now_ms()
        confirm_ms = max(0, now - int(self.state.request_started_ms or now))
        self.state.hold_until_ms = now + MIN_CONFIRMED_HOLD_MS
        self.state.retry_after_ms = 0
        self.state.last_confirmed_set = desired
        self.state.logical_set = desired
        self.state.logical_set_source = str(confirmation_source or "confirmed")
        self.state.failure_count = 0
        if desired in (BURST_SET, SUSTAIN_SET):
            self._learn_signature(desired, post_signature, source=confirmation_source)
        self._log(
            "MESMER_WEAPON_SWAP_CONFIRMED",
            requested_set=desired,
            energy_pct=f"{float(self.state.request_energy_pct):.3f}",
            post_weapon_type=post_type,
            post_weapon_name=post_name,
            confirmation_source=str(confirmation_source or ""),
            logical_set=int(self.state.logical_set or 0),
            confirm_ms=int(confirm_ms),
            hold_ms=MIN_CONFIRMED_HOLD_MS,
            keypress_attempts=int(self.state.keypress_attempts or 0),
            request_reason=pending_reason,
            **self._signature_fields(post_signature, "post"),
            **self._signature_fields(self.state.burst_signature, "burst"),
            **self._signature_fields(self.state.sustain_signature, "sustain"),
        )
        if desired == BURST_SET and pending_reason == "map_entry_force_f1":
            self._mark_map_f1_normalized(source=str(confirmation_source or "map_entry_confirmed_f1"), signature=post_signature)
        self._clear_pending()
        return True

    def _send_keypress(self, *, retry: bool) -> bool:
        desired = int(self.state.pending_set or 0)
        action = int(self.state.pending_action or self._action_for_set(desired))
        if desired not in (BURST_SET, SUSTAIN_SET):
            return self._fail_pending("invalid_pending_set")
        try:
            UIManager.Keypress(action, 0)
        except Exception as exc:
            return self._fail_pending("keypress_exception", error_type=type(exc).__name__)

        now = self._now_ms()
        self.state.keypress_attempts += 1
        self.state.last_keypress_ms = now
        if retry:
            current_signature = self._weapon_signature()
            self._log(
                "MESMER_WEAPON_SWAP_RETRY",
                requested_set=desired,
                energy_pct=f"{float(self.state.request_energy_pct):.3f}",
                attempt=int(self.state.keypress_attempts),
                retry_after_ms=KEYPRESS_RETRY_AFTER_MS,
                **self._signature_fields(current_signature, "current"),
                **self._signature_fields(self.state.pending_pre_signature, "pre"),
            )
        return True

    def _process_pending(self) -> bool:
        """Advance one existing transaction using exact equipped item signatures."""
        if int(self.state.pending_set or 0) <= 0:
            return False

        now = self._now_ms()
        desired = int(self.state.pending_set)
        event = self._new_swap_event(int(self.state.ui_log_start_index or 0))
        snapshot = self._equipment_snapshot()
        signature = snapshot.signature
        pre_signature = self.state.pending_pre_signature
        signature_valid = self._signature_valid(signature)

        if event == "cancel":
            return self._fail_pending("native_swap_cancel")

        if desired == BURST_SET:
            # F1 is the exact wand+focus set learned before the first F2 swap.
            if self._signature_valid(self.state.burst_signature) and signature == self.state.burst_signature:
                return self._confirm_pending(confirmation_source="exact_f1_item_signature", signature=signature, snapshot=snapshot)

            # Startup may have happened before a usable item snapshot.  A changed
            # signature with an offhand is a safe late-learning fallback for F1.
            if (
                not self._signature_valid(self.state.burst_signature)
                and signature_valid
                and self._signature_has_offhand(signature)
                and signature != pre_signature
            ):
                self._learn_signature(BURST_SET, signature, source="changed_to_wand_focus_after_f1")
                return self._confirm_pending(confirmation_source="learned_f1_item_signature", signature=signature, snapshot=snapshot)

        elif desired == SUSTAIN_SET:
            if self._signature_valid(self.state.sustain_signature) and signature == self.state.sustain_signature:
                return self._confirm_pending(confirmation_source="exact_f2_item_signature", signature=signature, snapshot=snapshot)

            # If item IDs already show the contractually two-handed F2 while the
            # logical weapon-type byte still says Scepter, do not hammer F2 again.
            if (
                signature_valid and not self._signature_has_offhand(signature)
                and self._signature_valid(self.state.burst_signature)
                and signature != self.state.burst_signature
            ):
                self._learn_signature(SUSTAIN_SET, signature, source="stable_no_offhand_contract_during_pending")
                if not self._snapshot_is_staff(snapshot):
                    self._log(
                        "MESMER_WEAPON_TELEMETRY_DIVERGENCE",
                        logical_weapon_type=int(snapshot.weapon_type),
                        logical_weapon_name=str(snapshot.weapon_name),
                        interpretation="item_signature_matches_F2_contract_while_weapon_type_lags",
                        **self._signature_fields(signature, "current"),
                    )
                return self._confirm_pending(
                    confirmation_source="f2_item_contract_already_present",
                    signature=signature, snapshot=snapshot,
                )

            # F2 is contractually the two-handed staff, so an actually changed
            # item signature with no offhand is strong positive evidence.
            if (
                signature_valid
                and signature != pre_signature
                and not self._signature_has_offhand(signature)
                and (not self._signature_valid(self.state.burst_signature) or signature != self.state.burst_signature)
            ):
                self._learn_signature(SUSTAIN_SET, signature, source="changed_to_staff_after_f2")
                return self._confirm_pending(confirmation_source="learned_f2_item_signature", signature=signature, snapshot=snapshot)

        # If the item API is temporarily unavailable, native completion remains a
        # fallback.  When valid item IDs are available, exact signatures win and a
        # stale/unchanged snapshot cannot produce a false CONFIRMED.
        if event == "complete" and not signature_valid:
            return self._confirm_pending(confirmation_source="native_swap_complete_no_item_data", signature=signature, snapshot=snapshot)

        # Map-entry F1 normalization exists specifically because item/weapon
        # telemetry can remain stale across zoning. For this one bounded F1
        # transaction, a native swap-complete event is therefore sufficient even
        # if GetWeaponExtraData still reports the pre-zoning no-offhand signature.
        if event == "complete" and str(self.state.pending_reason or "") == "map_entry_force_f1":
            return self._confirm_pending(confirmation_source="native_map_entry_f1_complete", signature=signature, snapshot=snapshot)

        # One controlled re-pulse handles an occasional swallowed F1/F2 keypress.
        if (
            int(self.state.keypress_attempts or 0) < MAX_KEYPRESS_ATTEMPTS
            and now - int(self.state.last_keypress_ms or now) >= KEYPRESS_RETRY_AFTER_MS
            and self._safe_to_start_swap()
        ):
            return self._send_keypress(retry=True)

        if now - int(self.state.request_started_ms or now) >= CONFIRM_TIMEOUT_MS:
            return self._fail_pending("item_signature_not_confirmed")
        return True

    def _start_swap(self, desired: int, energy_pct: float, in_combat: bool, *, reason: str = "energy_hysteresis") -> bool:
        now = self._now_ms()
        action = self._action_for_set(desired)
        pre_snapshot = self._equipment_snapshot()
        pre_type, pre_name = int(pre_snapshot.weapon_type), str(pre_snapshot.weapon_name)
        pre_signature = pre_snapshot.signature

        # Learn F1 before leaving it.  The user's F1 contract is wand+focus, so an
        # offhand-positive snapshot is the strongest baseline for exact return.
        if (
            desired == SUSTAIN_SET
            and not self._signature_valid(self.state.burst_signature)
            and self._signature_valid(pre_signature)
            and self._signature_has_offhand(pre_signature)
        ):
            self._learn_signature(BURST_SET, pre_signature, source="baseline_before_first_f2")

        # Likewise, if an F1 return begins before F2 was formally learned, retain
        # the current no-offhand signature as the expected sustain set.
        if (
            desired == BURST_SET
            and not self._signature_valid(self.state.sustain_signature)
            and self._signature_valid(pre_signature)
            and not self._signature_has_offhand(pre_signature)
        ):
            self._learn_signature(SUSTAIN_SET, pre_signature, source="baseline_before_f1_return")

        # Idempotence: exact equipment evidence outranks a stale weapon-type byte.
        if (
            desired == SUSTAIN_SET
            and self._signature_valid(pre_signature)
            and not self._signature_has_offhand(pre_signature)
            and self._signature_valid(self.state.burst_signature)
            and pre_signature != self.state.burst_signature
        ):
            self._learn_signature(SUSTAIN_SET, pre_signature, source="pre_request_no_offhand_contract")
            self.state.logical_set = SUSTAIN_SET
            self.state.logical_set_source = "pre_request_item_contract"
            if not self._snapshot_is_staff(pre_snapshot):
                self._log(
                    "MESMER_WEAPON_TELEMETRY_DIVERGENCE",
                    logical_weapon_type=int(pre_snapshot.weapon_type),
                    logical_weapon_name=str(pre_snapshot.weapon_name),
                    interpretation="skip_redundant_F2_keypress_item_signature_authoritative",
                    **self._signature_fields(pre_signature, "current"),
                )
            return False

        self.state.last_requested_set = int(desired)
        self.state.pending_set = int(desired)
        self.state.pending_action = int(action)
        self.state.pending_phase = "confirm"
        self.state.request_started_ms = now
        self.state.phase_started_ms = now
        self.state.ui_log_start_index = self._ui_log_len()
        self.state.request_energy_pct = float(energy_pct)
        self.state.request_in_combat = bool(in_combat)
        self.state.pending_previous_logical_set = int(self.state.logical_set or self._effective_current_set() or 0)
        self.state.pending_pre_signature = pre_signature
        self.state.keypress_attempts = 0
        self.state.last_keypress_ms = 0
        self.state.pending_reason = str(reason or "energy_hysteresis")

        self._log(
            "MESMER_WEAPON_SWAP_REQUEST",
            requested_set=int(desired),
            energy_pct=f"{float(energy_pct):.3f}",
            in_combat=bool(in_combat),
            pre_weapon_type=pre_type,
            pre_weapon_name=pre_name,
            low_threshold=f"{LOW_ENERGY_THRESHOLD:.2f}",
            high_threshold=f"{HIGH_ENERGY_THRESHOLD:.2f}",
            confirm_timeout_ms=CONFIRM_TIMEOUT_MS,
            input_method="UIManager.Keypress",
            max_keypress_attempts=MAX_KEYPRESS_ATTEMPTS,
            retry_after_ms=KEYPRESS_RETRY_AFTER_MS,
            policy="F1_40_40_F2_SR5_map_entry_F1_then_35_to_75_exact_item_signature",
            request_reason=str(self.state.pending_reason or ""),
            **self._signature_fields(pre_signature, "pre"),
            **self._signature_fields(self.state.burst_signature, "burst"),
            **self._signature_fields(self.state.sustain_signature, "sustain"),
        )

        if not self._send_keypress(retry=False):
            return False

        # Logical memory still allows the high-energy return decision to form, but
        # Stage 1.14 will not call a swap CONFIRMED until item IDs prove it.
        self.state.logical_set = int(desired)
        self.state.logical_set_source = "keypress_sent_pending_item_confirmation"
        self._log(
            "MESMER_WEAPON_LOGICAL_SET",
            logical_set=int(self.state.logical_set),
            source=str(self.state.logical_set_source),
            energy_pct=f"{float(energy_pct):.3f}",
            requested_set=int(desired),
        )
        return True

    def _telemetry(self, energy_pct: float, in_combat: bool) -> None:
        now = self._now_ms()
        snapshot = self._equipment_snapshot()
        weapon_type, weapon_name = int(snapshot.weapon_type), str(snapshot.weapon_name)
        actual_staff = self._snapshot_is_staff(snapshot)
        signature = snapshot.signature

        # Capture the F1 baseline as soon as a real wand+focus snapshot exists.
        if (
            int(self.state.pending_set or 0) == 0
            and not self._signature_valid(self.state.burst_signature)
            and self._signature_valid(signature)
            and self._signature_has_offhand(signature)
        ):
            self._learn_signature(BURST_SET, signature, source="startup_wand_focus_baseline")
            if int(self.state.logical_set or 0) == 0:
                self.state.logical_set = BURST_SET
                self.state.logical_set_source = "startup_f1_signature"

        # F2 contract recovery: a stable valid no-offhand signature that differs
        # from learned F1 is stronger evidence than a lagging weapon_type byte.
        if (
            int(self.state.pending_set or 0) == 0
            and not self._signature_valid(self.state.sustain_signature)
            and self._signature_valid(signature)
            and not self._signature_has_offhand(signature)
            and self._signature_valid(self.state.burst_signature)
            and signature != self.state.burst_signature
        ):
            self._learn_signature(SUSTAIN_SET, signature, source="observed_no_offhand_F2_contract")
            self.state.logical_set = SUSTAIN_SET
            self.state.logical_set_source = "observed_item_contract_over_weapon_type"
            if not actual_staff:
                self._log(
                    "MESMER_WEAPON_TELEMETRY_DIVERGENCE",
                    logical_weapon_type=int(weapon_type), logical_weapon_name=str(weapon_name),
                    interpretation="item_signature_F2_weapon_type_stale",
                    **self._signature_fields(signature, "current"),
                )

        # Exact signature reconciliation is authoritative once learned.
        if int(self.state.pending_set or 0) == 0 and self._signature_valid(signature):
            if self._signature_valid(self.state.burst_signature) and signature == self.state.burst_signature:
                if int(self.state.logical_set or 0) != BURST_SET:
                    self.state.logical_set = BURST_SET
                    self.state.logical_set_source = "observed_exact_f1_signature"
                    self._log(
                        "MESMER_WEAPON_LOGICAL_SET",
                        logical_set=BURST_SET,
                        source=self.state.logical_set_source,
                        energy_pct=f"{float(energy_pct):.3f}",
                        requested_set=0,
                    )
            elif self._signature_valid(self.state.sustain_signature) and signature == self.state.sustain_signature:
                if int(self.state.logical_set or 0) != SUSTAIN_SET:
                    self.state.logical_set = SUSTAIN_SET
                    self.state.logical_set_source = "observed_exact_f2_signature"
                    self._log(
                        "MESMER_WEAPON_LOGICAL_SET",
                        logical_set=SUSTAIN_SET,
                        source=self.state.logical_set_source,
                        energy_pct=f"{float(energy_pct):.3f}",
                        requested_set=0,
                    )

        desired = int(self._desired_set(float(energy_pct), bool(in_combat)) or 0)

        if not bool(self.state.active_logged):
            self.state.active_logged = True
            self._log(
                "MESMER_WEAPON_CONTROLLER_ACTIVE",
                controller_version="stage1.27_map_entry_f1",
                burst_set=BURST_SET,
                sustain_set=SUSTAIN_SET,
                low_threshold=f"{LOW_ENERGY_THRESHOLD:.2f}",
                high_threshold=f"{HIGH_ENERGY_THRESHOLD:.2f}",
                weapon_type=weapon_type,
                weapon_name=weapon_name,
                actual_staff=bool(actual_staff),
                logical_set=int(self.state.logical_set or 0),
                **self._signature_fields(signature, "current"),
                **self._signature_fields(self.state.burst_signature, "burst"),
                **self._signature_fields(self.state.sustain_signature, "sustain"),
            )

        if now - int(self.state.last_state_log_ms or 0) >= STATE_HEARTBEAT_MS:
            self.state.last_state_log_ms = now
            self._log(
                "MESMER_WEAPON_STATE",
                energy_pct=f"{float(energy_pct):.3f}",
                in_combat=bool(in_combat),
                weapon_type=weapon_type,
                weapon_name=weapon_name,
                actual_staff=bool(actual_staff),
                logical_set=int(self.state.logical_set or 0),
                logical_source=str(self.state.logical_set_source or ""),
                effective_set=int(self._effective_current_set() or 0),
                desired_set=desired,
                pending_set=int(self.state.pending_set or 0),
                keypress_attempts=int(self.state.keypress_attempts or 0),
                **self._signature_fields(signature, "current"),
                **self._signature_fields(self.state.burst_signature, "burst"),
                **self._signature_fields(self.state.sustain_signature, "sustain"),
            )

        # Keep the old positive staff observation only as a fallback before an F2
        # signature exists.  Exact item signatures supersede weapon-type telemetry.
        if (
            actual_staff
            and not self._signature_valid(self.state.sustain_signature)
            and int(self.state.logical_set or 0) != SUSTAIN_SET
            and int(self.state.pending_set or 0) == 0
        ):
            self.state.logical_set = SUSTAIN_SET
            self.state.logical_set_source = "observed_staff_fallback"
            self._log(
                "MESMER_WEAPON_LOGICAL_SET",
                logical_set=SUSTAIN_SET,
                source="observed_staff_fallback",
                energy_pct=f"{float(energy_pct):.3f}",
                requested_set=0,
            )

    def maybe_switch(self, energy_pct: float, *, in_combat: bool):
        """One-tick persistent state-machine step.

        This deliberately *contains no real yield*.  The unreachable yield keeps
        backward compatibility with existing ``yield from maybe_switch(...)``
        call sites while ensuring every invocation completes in the same HeroAI
        tick.  All multi-frame state lives in ``self.state``.
        """
        if False:  # pragma: no cover - generator compatibility only
            yield None

        self._observe_map_entry()
        self._telemetry(float(energy_pct), bool(in_combat))

        # Stage 1.27: every new explorable instance starts from an explicit F1
        # baseline. This has priority over the ordinary Energy hysteresis but is
        # bounded/fail-open, so a broken F1 configuration cannot stall combat.
        if not self.state.map_f1_normalized and int(self.state.last_map_id or 0) > 0:
            if self._map_entry_normalization_step(float(energy_pct), bool(in_combat)):
                return True
            if not self.state.map_f1_normalized:
                return False

        # Existing transaction owns the slot until confirmed/failed; this is what
        # prevents the repeated REQUEST spam seen in Stage 1.10 logs.
        if int(self.state.pending_set or 0) > 0:
            return self._process_pending()

        now = self._now_ms()
        if now < int(self.state.hold_until_ms or 0):
            return False
        if now < int(self.state.retry_after_ms or 0):
            return False

        desired = int(self._desired_set(float(energy_pct), bool(in_combat)) or 0)
        if desired <= 0:
            return False
        if not self._safe_to_start_swap():
            return False

        return self._start_swap(desired, float(energy_pct), bool(in_combat))
