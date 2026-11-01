from __future__ import annotations

from typing import TYPE_CHECKING

from Py4GWCoreLib.BuildMgr import BuildCoroutine
from Py4GWCoreLib import Range, Routines, ThrottledTimer
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.GlobalCache.WhiteboardLocks import (
    BLOOD_ENERGY_BUFF_LOCK_KEY,
    is_buff_target_lock_blocked,
    post_buff_target_lock,
)
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.Skill import Skill

if TYPE_CHECKING:
    from Py4GWCoreLib.HeroAI.custom_skill_src.skill_types import CustomSkill
    from Py4GWCoreLib.BuildMgr import BuildMgr

__all__ = ["BloodMagic"]


class BloodMagic:
    def __init__(self, build: BuildMgr) -> None:
        self.build: BuildMgr = build
        self.bip_throttle: ThrottledTimer = ThrottledTimer(1500)
        self.bip_throttle.Stop()
        # Shared effect visibility can lag on other multibox accounts. Keep a
        # local per-target lease so one BiP caster cannot reapply the 10-second
        # enchantment every 1.5-3 seconds to the same ally.
        self._bip_recent_target_until: dict[int, int] = {}
        # Stage 1.80: queue acceptance is not proof that BiP landed.  Keep a
        # short pending lease until the target-side enchantment is observed; only
        # then promote to the normal long anti-reapply lease.
        self._bip_pending_target_until: dict[int, int] = {}
        self._bip_pending_started_ms: dict[int, int] = {}
        # Stage 2.24: target-side effect telemetry is unreliable for native
        # heroes. Keep the action-queue dispatch time so caster-side casting /
        # recharge evidence can confirm those casts without blindly trusting a queue.
        self._bip_dispatched_ms: dict[int, int] = {}
        self._bip_last_dispatch_target: int = 0
        # Compatibility marker retained for Stage 2.25 regression coverage:
        # stage2_25_target_effect_or_live_caster_recharge_confirmation
        # Stage 2.26: SkillbarSkill.recharge is a cast timestamp in this runtime.
        # Snapshot it before dispatch so native-hero BiP can be confirmed by an
        # actual timestamp transition even when target effects/get_recharge are invisible.
        self._bip_precast_recharge_stamp: dict[int, int] = {}
        # Stage 2.32: native-hero target effects are not visible on this client.
        # Bind one caster-side health-sacrifice receipt to the exact last dispatch
        # target/generation; this is stronger than queue admission and survives
        # runtimes where skillbar recharge telemetry remains stale.
        self._bip_precast_hp_fraction: dict[int, float] = {}
        self._bip_expected_sacrifice_fraction: dict[int, float] = {}
        # Stage 1.7: sustained-combat BiP support.  We keep the normal 75%
        # trigger at the opening, then proactively raise it once the encounter
        # proves long enough to justify keeping the caster block buffered.
        self._bip_combat_started_ms: int = 0
        self._bip_pending_confirm_ms: int = 4500
        self.blood_ritual_throttle: ThrottledTimer = ThrottledTimer(1500)
        self.blood_ritual_throttle.Stop()

    #region B
    def Blood_Bond(self) -> BuildCoroutine:
        blood_bond_id: int = Skill.GetID("Blood_Bond")

        if not self.build.IsSkillEquipped(blood_bond_id):
            return False
        if not self.build.IsInAggro():
            return False

        target_agent_id = Routines.Targeting.PickClusteredTarget(
            cluster_radius=Range.Adjacent.value,
            preferred_condition=lambda agent_id: not Routines.Checks.Agents.HasEffect(agent_id, blood_bond_id),
            filter_radius=Range.Spellcast.value,
        )
        if not target_agent_id:
            return False
        if Routines.Checks.Agents.HasEffect(target_agent_id, blood_bond_id):
            return False

        return (yield from self.build.CastSkillIDAndRestoreTarget(
            blood_bond_id,
            target_agent_id,
            log=False,
            aftercast_delay=250,
        ))

    def Blood_Ritual(self) -> BuildCoroutine:
        blood_ritual_id: int = Skill.GetID("Blood_Ritual")
        blood_ritual: CustomSkill = self.build.GetCustomSkill(blood_ritual_id)

        def _is_blood_ritual_throttle_ready() -> bool:
            return self.blood_ritual_throttle.IsStopped() or self.blood_ritual_throttle.IsExpired()

        def _can_safely_cast_blood_ritual() -> bool:
            player_id = Player.GetAgentID()
            current_hp_fraction = float(Agent.GetHealth(player_id))
            sacrifice_floor = float(blood_ritual.Conditions.SacrificeHealth or 0.0)
            return current_hp_fraction > sacrifice_floor

        if not self.build.IsSkillEquipped(blood_ritual_id):
            return False

        target_agent_id: int = self.build.ResolveAllyTarget(
            blood_ritual_id,
            blood_ritual,
        )
        if not target_agent_id:
            return False
        if is_buff_target_lock_blocked(target_agent_id, BLOOD_ENERGY_BUFF_LOCK_KEY):
            return False
        if not _is_blood_ritual_throttle_ready():
            return False
        if not _can_safely_cast_blood_ritual():
            return False

        post_buff_target_lock(
            target_agent_id,
            key_id=BLOOD_ENERGY_BUFF_LOCK_KEY,
            skill_id=blood_ritual_id,
        )
        if (yield from self.build.CastSkillIDAndRestoreTarget(
            blood_ritual_id,
            target_agent_id,
            extra_condition=_can_safely_cast_blood_ritual,
        )):
            self.blood_ritual_throttle.Reset()
            return True

        return False

    def _is_native_party_hero(self, agent_id: int) -> bool:
        aid = int(agent_id or 0)
        if aid <= 0:
            return False
        try:
            from Py4GWCoreLib.Party import Party
            return any(int(getattr(hero, "agent_id", 0) or 0) == aid for hero in (Party.GetHeroes() or []))
        except Exception:
            return False


    def _bip_skillbar_recharge_snapshot(self, blood_is_power_id: int) -> tuple[int, int]:
        """Return a *fresh* (cast_timestamp, remaining_recharge_ms) for BiP.

        Stage 2.26-2.28 read the long-lived GLOBAL_CACHE Skillbar instance without
        refreshing its PySkillbar context in this path. Target effects proved the
        casts landed on players, while native-hero receipts stayed 0/26 because
        the caster-side recharge/timestamp snapshot could remain stale at zero.
        Stage 2.29 reads a newly-contextualized PySkillbar object first, then keeps
        the old cache path only as a compatibility fallback.
        """
        sid = int(blood_is_power_id or 0)
        if sid <= 0:
            return 0, 0
        try:
            import PySkillbar
            live = PySkillbar.Skillbar()
            live.GetContext()
            for slot in range(1, 9):
                data = live.GetSkill(int(slot))
                try:
                    live_sid = int(getattr(getattr(data, "id", None), "id", 0) or 0)
                except Exception:
                    live_sid = 0
                if live_sid != sid:
                    continue
                stamp = int(getattr(data, "recharge", 0) or 0)
                raw_remaining = getattr(data, "get_recharge", None)
                remaining = int(raw_remaining or 0)
                if 0x80000000 <= remaining <= 0xFFFFFFFF:
                    remaining -= 0x100000000
                return int(stamp), max(0, int(remaining))
        except Exception:
            pass
        try:
            from Py4GWCoreLib import GLOBAL_CACHE
            # Best-effort context refresh for older runtimes exposing only the
            # shared cache wrapper. This is intentionally a fallback, not the
            # primary Stage-2.29 receipt source.
            try:
                GLOBAL_CACHE.SkillBar._update_cache()
            except Exception:
                pass
            slot = int(GLOBAL_CACHE.SkillBar.GetSlotBySkillID(sid) or 0)
            if slot <= 0:
                return 0, 0
            skillbar_data = GLOBAL_CACHE.SkillBar.GetSkillData(slot)
            stamp = int(getattr(skillbar_data, "recharge", 0) or 0)
            raw_remaining = getattr(skillbar_data, "get_recharge", None)
            remaining = int(raw_remaining or 0)
            if 0x80000000 <= remaining <= 0xFFFFFFFF:
                remaining -= 0x100000000
            return int(stamp), max(0, int(remaining))
        except Exception:
            return 0, 0

    def _bip_caster_receipt_source(self, blood_is_power_id: int, target_agent_id: int, now: int) -> str:
        """Return strong local BiP proof for native heroes, never queue admission.

        Target effects remain the primary proof. For the latest dispatched target,
        prefer a native finish event; reject observed interruption and active casts.
        A changed cast stamp or positive live recharge after casting ends is a
        bounded fallback, not direct evidence of the target's buff.
        """
        aid = int(target_agent_id or 0)
        dispatched = int(self._bip_dispatched_ms.get(aid, 0) or 0)
        if dispatched <= 0 or int(now) < dispatched:
            return ""
        if aid != self._bip_last_dispatch_target:
            return ""
        # Keep receipt evidence bounded to the same pending-confirmation window.
        if int(now) - int(dispatched) > int(self._bip_pending_confirm_ms) + 1000:
            return ""
        try:
            from Py4GWCoreLib.Builds.Skills.ReforgedSupport import get_cast_outcome
            outcome = get_cast_outcome(int(Player.GetAgentID() or 0), int(blood_is_power_id), dispatched)
            if outcome in ("interrupted", "stopped"):
                return ""
            if outcome == "finished":
                return "native_hero_caster_finished"
        except Exception:
            pass
        try:
            if Agent.IsCasting(int(Player.GetAgentID() or 0)):
                return ""
        except Exception:
            return ""
        try:
            cast_stamp, remaining = self._bip_skillbar_recharge_snapshot(int(blood_is_power_id))
            before_stamp = int(self._bip_precast_recharge_stamp.get(aid, 0) or 0)
            if int(cast_stamp) > 0 and int(cast_stamp) != int(before_stamp):
                return "native_hero_caster_cast_stamp"
            if int(remaining) > 0 and int(now) - dispatched >= 250:
                return "native_hero_caster_recharge"
        except Exception:
            pass
        # Blood is Power pays a large health sacrifice on successful activation.
        # When native target-effect/recharge visibility is unavailable, a prompt
        # caster HP drop matching that sacrifice is generation-bound evidence for
        # the *last dispatched target only*.  Do not use it while still casting,
        # after the pending window, or for an older target.
        try:
            age = int(now) - int(dispatched)
            before_hp = float(self._bip_precast_hp_fraction.get(aid, -1.0))
            expected = float(self._bip_expected_sacrifice_fraction.get(aid, 0.33) or 0.33)
            current_hp = float(Agent.GetHealth(int(Player.GetAgentID() or 0)) or 0.0)
            required_drop = max(0.16, min(0.30, expected * 0.55))
            observed_drop = before_hp - current_hp
            if 180 <= age <= min(3200, int(self._bip_pending_confirm_ms)) and before_hp > 0.0 and current_hp > 0.0 and observed_drop >= required_drop:
                return "native_hero_caster_sacrifice_receipt"
        except Exception:
            pass
        return ""

    def _promote_bip_lease(self, aid: int, started: int, now: int, blood_is_power_id: int, source: str) -> None:
        aid = int(aid)
        self._bip_recent_target_until[aid] = max(int(now) + 500, int(started) + 10500)
        self._bip_pending_target_until.pop(aid, None)
        self._bip_pending_started_ms.pop(aid, None)
        self._bip_dispatched_ms.pop(aid, None)
        self._bip_precast_recharge_stamp.pop(aid, None)
        self._bip_precast_hp_fraction.pop(aid, None)
        self._bip_expected_sacrifice_fraction.pop(aid, None)
        # Refresh the shared target lock as well as the local lease so any future
        # second BiP caster sees the same provisional native-hero confirmation.
        try:
            post_buff_target_lock(aid, key_id=BLOOD_ENERGY_BUFF_LOCK_KEY, skill_id=int(blood_is_power_id))
        except Exception:
            pass
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_BIP_CONFIRMED", target_id=aid,
                confirmation_delay_ms=max(0, int(now - started)),
                confirmation_source=str(source), native_hero=bool(self._is_native_party_hero(aid)),
                target_lease_until=int(self._bip_recent_target_until[aid]),
                policy="stage2_32_target_effect_or_generation_bound_caster_finish_recharge_sacrifice",
            )
        except Exception:
            pass

    def _reconcile_bip_pending_effects(self, blood_is_power_id: int) -> None:
        """Promote target-effect or native-hero caster-receipt proof to the long lease."""
        try:
            import PySystem
            from Py4GWCoreLib.HeroAI.utils import CheckForEffect

            now = int(PySystem.get_tick_count64() or 0)
            if now <= 0:
                return
            for aid, deadline in list(self._bip_pending_target_until.items()):
                aid = int(aid)
                started = int(self._bip_pending_started_ms.get(aid, now) or now)
                observed = False
                try:
                    observed = bool(CheckForEffect(aid, int(blood_is_power_id)))
                except Exception:
                    observed = False
                if observed:
                    self._promote_bip_lease(
                        aid, started, now, int(blood_is_power_id), "target_effect"
                    )
                    continue
                if self._is_native_party_hero(aid):
                    receipt_source = self._bip_caster_receipt_source(
                        int(blood_is_power_id), aid, now
                    )
                    if receipt_source:
                        self._promote_bip_lease(
                            aid, started, now, int(blood_is_power_id), str(receipt_source)
                        )
                        continue
                if int(deadline or 0) <= now:
                    self._bip_pending_target_until.pop(aid, None)
                    self._bip_pending_started_ms.pop(aid, None)
                    self._bip_dispatched_ms.pop(aid, None)
                    self._bip_precast_recharge_stamp.pop(aid, None)
                    self._bip_precast_hp_fraction.pop(aid, None)
                    self._bip_expected_sacrifice_fraction.pop(aid, None)
                    # A failed/unknown activation must not inherit a previous
                    # queue-based long lease from this attempt.
                    if int(self._bip_recent_target_until.get(aid, 0) or 0) <= now:
                        self._bip_recent_target_until.pop(aid, None)
                    try:
                        from Py4GWCoreLib.Builds.Skills import CombatDebug
                        CombatDebug.log_event(
                            "MESMERWAY_BIP_UNCONFIRMED_RELEASED", target_id=aid,
                            waited_ms=max(0, int(now - started)),
                            policy="stage2_11_extended_visibility_window_failed_or_unknown_reopens_target",
                        )
                    except Exception:
                        pass
        except Exception:
            return

    def _resolve_bip_target_with_recent_lease(self, blood_is_power_id: int, less_energy: float) -> int:
        try:
            import PySystem
            from Py4GWCoreLib import AgentArray, Range, Utils
            from Py4GWCoreLib.HeroAI.targeting import FilterAllyArray
            from Py4GWCoreLib.HeroAI.utils import CheckForEffect, GetEnergyValues, IsValidEnergyValue

            now = int(PySystem.get_tick_count64() or 0)
            self._reconcile_bip_pending_effects(int(blood_is_power_id))
            # Opportunistically prune old confirmed entries.
            self._bip_recent_target_until = {
                int(aid): int(expiry)
                for aid, expiry in self._bip_recent_target_until.items()
                if int(expiry) > now
            }

            allies = AgentArray.GetAllyArray()
            allies = FilterAllyArray(
                allies,
                Range.Spellcast.value,
                other_ally=True,
                filter_skill_id=int(blood_is_power_id),
            )
            rows: list[tuple[float, float, int]] = []
            player_xy = Player.GetXY()
            for ally_id in allies or []:
                ally_id = int(ally_id)
                if int(self._bip_pending_target_until.get(ally_id, 0) or 0) > now:
                    continue
                if int(self._bip_recent_target_until.get(ally_id, 0) or 0) > now:
                    continue
                if CheckForEffect(ally_id, int(blood_is_power_id)):
                    continue
                if is_buff_target_lock_blocked(ally_id, BLOOD_ENERGY_BUFF_LOCK_KEY):
                    continue
                energy = float(GetEnergyValues(ally_id))
                if not IsValidEnergyValue(energy) or energy > float(less_energy):
                    continue
                try:
                    distance = float(Utils.Distance(Agent.GetXY(ally_id), player_xy))
                except Exception:
                    distance = 999999.0
                rows.append((energy, distance, ally_id))
            rows.sort()
            return int(rows[0][2]) if rows else 0
        except Exception:
            return 0

    def GetUrgentBiPRecoveryNeed(self) -> tuple[int, float]:
        """Diagnose a safety-blocked urgent BiP without changing BiP thresholds.

        Uses the existing recipient selector, target leases and equipped skill
        conditions.  The returned recipient is diagnostic only; BiP selects
        and validates a *fresh* recipient after any self-heal has completed.
        """
        skill_id = int(Skill.GetID("Blood_is_Power") or 0)
        if not skill_id or not self.build.IsSkillEquipped(skill_id):
            return 0, 0.0
        if not (self.bip_throttle.IsStopped() or self.bip_throttle.IsExpired()):
            return 0, 0.0
        if not self.build.CanCastSkillID(skill_id):
            return 0, 0.0
        # Unlike regular proactive 75/80/85% BiP, recovery is only activated
        # when a party member is truly energy-starved (<35%).
        candidate = int(self._resolve_bip_target_with_recent_lease(skill_id, 0.35) or 0)
        if not candidate or is_buff_target_lock_blocked(candidate, BLOOD_ENERGY_BUFF_LOCK_KEY):
            return 0, 0.0
        player_id = int(Player.GetAgentID() or 0)
        if player_id <= 0 or candidate == player_id or not Agent.IsAlive(player_id):
            return 0, 0.0
        try:
            max_hp = float(Agent.GetMaxHealth(player_id))
            if max_hp <= 0:
                return 0, 0.0
            hp = float(Agent.GetHealth(player_id))
            conditions = self.build.GetCustomSkill(skill_id).Conditions
            sacrifice = float(conditions.SacrificePercent or 0.33)
            min_after_hp_pct = float(conditions.MinHealthAfterSacrificePercent or 0.0)
            min_after_hp_abs = float(conditions.MinHealthAfterSacrificeAbsolute or 0.0)
            required = max(
                float(conditions.SacrificeHealth or 0.0),
                sacrifice + min_after_hp_pct,
                sacrifice + min_after_hp_abs / max_hp,
            )
            if required >= 0.99 or hp > required:
                return 0, 0.0
            return candidate, required
        except Exception:
            return 0, 0.0

    def _mesmerway_bip_threshold(self) -> tuple[float, str, int, int, float, float]:
        """Return adaptive BiP threshold for this sustained caster team.

        Opening: 75% (existing policy).
        Sustained combat >= 6 s: 80% so BiP starts buffering before anyone is dry.
        Heavy sustained pressure >= 15 s: 85% only when the party energy snapshot
        proves that the caster block is actually consuming Energy.  We never
        blanket-cast on full-Energy allies, and the existing effect + 10.5 s
        per-target lease still prevents wasteful reapplication.
        """
        try:
            import PySystem
            from Py4GWCoreLib import AgentArray, Range
            from Py4GWCoreLib.HeroAI.targeting import FilterAllyArray
            from Py4GWCoreLib.HeroAI.utils import GetEnergyValues, IsValidEnergyValue

            now = int(PySystem.get_tick_count64() or 0)
            in_combat = bool(self.build.IsInAggro())
            if in_combat:
                if int(self._bip_combat_started_ms or 0) <= 0:
                    self._bip_combat_started_ms = now
            else:
                self._bip_combat_started_ms = 0

            combat_ms = max(0, now - int(self._bip_combat_started_ms or now)) if in_combat else 0
            values: list[float] = []
            try:
                allies = FilterAllyArray(
                    AgentArray.GetAllyArray(),
                    Range.Spellcast.value,
                    other_ally=True,
                )
                for ally_id in allies or []:
                    try:
                        value = float(GetEnergyValues(int(ally_id)))
                        if IsValidEnergyValue(value):
                            values.append(max(0.0, min(1.0, value)))
                    except Exception:
                        continue
            except Exception:
                values = []

            low80 = sum(1 for value in values if value < 0.80)
            min_energy = min(values) if values else 1.0
            avg_energy = (sum(values) / len(values)) if values else 1.0

            threshold = 0.75
            mode = "opening_75"
            if in_combat and combat_ms >= 6000:
                threshold = 0.80
                mode = "sustained_80"
            if in_combat and combat_ms >= 15000 and (low80 >= 2 or avg_energy < 0.82 or min_energy < 0.60):
                threshold = 0.85
                mode = "heavy_sustained_85"
            return float(threshold), str(mode), int(combat_ms), int(low80), float(min_energy), float(avg_energy)
        except Exception:
            return 0.75, "fallback_75", 0, 0, 1.0, 1.0

    def Blood_is_Power(self) -> BuildCoroutine:
        blood_is_power_id: int = Skill.GetID("Blood_is_Power")
        blood_is_power: CustomSkill = self.build.GetCustomSkill(blood_is_power_id)

        def _is_bip_throttle_ready() -> bool:
            return self.bip_throttle.IsStopped() or self.bip_throttle.IsExpired()

        def _can_safely_cast_bip() -> bool:
            # Refuse if the caster's HP after the BiP sacrifice would land at or
            # below the percent-of-max floor or the absolute-HP floor. Mirrors the
            # post-sacrifice safety check in Py4GWCoreLib/HeroAI/combat.py so the build path
            # honors the same floors as the HeroAI fallback.
            player_id = Player.GetAgentID()
            conditions = blood_is_power.Conditions

            current_hp_fraction = float(Agent.GetHealth(player_id))
            sacrifice_floor = float(conditions.SacrificeHealth or 0.0)
            if current_hp_fraction <= sacrifice_floor:
                return False

            sacrifice_pct = float(conditions.SacrificePercent or 0.0)
            min_after_pct = float(conditions.MinHealthAfterSacrificePercent or 0.0)
            min_after_abs = int(conditions.MinHealthAfterSacrificeAbsolute or 0)
            if sacrifice_pct > 0 and (min_after_pct > 0 or min_after_abs > 0):
                max_hp = Agent.GetMaxHealth(player_id)
                if max_hp <= 0:
                    return False
                sacrifice_amount = max_hp * sacrifice_pct
                hp_after_sacrifice = (current_hp_fraction * max_hp) - sacrifice_amount
                if min_after_abs > 0 and hp_after_sacrifice <= min_after_abs:
                    return False
                if min_after_pct > 0 and (hp_after_sacrifice / max_hp) <= min_after_pct:
                    return False

            return True

        if not self.build.IsSkillEquipped(blood_is_power_id):
            return False

        bip_threshold, bip_mode, bip_combat_ms, bip_low80, bip_min_energy, bip_avg_energy = (
            self._mesmerway_bip_threshold()
        )
        target_agent_id: int = self._resolve_bip_target_with_recent_lease(
            blood_is_power_id,
            float(bip_threshold),
        )
        if not target_agent_id:
            return False
        if is_buff_target_lock_blocked(target_agent_id, BLOOD_ENERGY_BUFF_LOCK_KEY):
            return False
        if not _is_bip_throttle_ready():
            return False
        if not _can_safely_cast_bip():
            return False

        post_buff_target_lock(
            target_agent_id,
            key_id=BLOOD_ENERGY_BUFF_LOCK_KEY,
            skill_id=blood_is_power_id,
        )
        try:
            from Py4GWCoreLib.HeroAI.utils import GetEnergyValues
            _target_energy_before = float(GetEnergyValues(target_agent_id))
        except Exception:
            _target_energy_before = -1.0

        _precast_stamp, _precast_remaining = self._bip_skillbar_recharge_snapshot(int(blood_is_power_id))
        self._bip_precast_recharge_stamp[int(target_agent_id)] = int(_precast_stamp)
        try:
            self._bip_precast_hp_fraction[int(target_agent_id)] = float(Agent.GetHealth(int(Player.GetAgentID() or 0)) or 0.0)
        except Exception:
            self._bip_precast_hp_fraction[int(target_agent_id)] = -1.0
        try:
            _expected_sacrifice = float(blood_is_power.Conditions.SacrificePercent or 0.0)
            if _expected_sacrifice <= 0.0:
                _expected_sacrifice = 0.33
            self._bip_expected_sacrifice_fraction[int(target_agent_id)] = float(_expected_sacrifice)
        except Exception:
            self._bip_expected_sacrifice_fraction[int(target_agent_id)] = 0.33

        def _mark_bip_dispatched() -> None:
            try:
                import PySystem
                self._bip_dispatched_ms[int(target_agent_id)] = int(PySystem.get_tick_count64() or 0)
                self._bip_last_dispatch_target = int(target_agent_id)
            except Exception:
                self._bip_dispatched_ms[int(target_agent_id)] = 0

        if (yield from self.build.CastSkillIDAndRestoreTarget(
            blood_is_power_id,
            target_agent_id,
            extra_condition=_can_safely_cast_bip,
            on_dispatched=_mark_bip_dispatched,
        )):
            self.bip_throttle.Reset()
            try:
                import PySystem
                now = int(PySystem.get_tick_count64() or 0)
                if now > 0:
                    self._bip_pending_started_ms[int(target_agent_id)] = int(now)
                    self._bip_pending_target_until[int(target_agent_id)] = int(now) + int(self._bip_pending_confirm_ms)
            except Exception:
                pass
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_BIP_QUEUED",
                    target_id=int(target_agent_id),
                    target_energy_pct=(
                        f"{_target_energy_before:.3f}"
                        if 0.0 <= _target_energy_before <= 1.0 else "unknown"
                    ),
                    threshold=f"{float(bip_threshold):.2f}",
                    bip_mode=str(bip_mode),
                    combat_ms=int(bip_combat_ms),
                    party_below_80=int(bip_low80),
                    party_min_energy=f"{bip_min_energy:.3f}",
                    party_avg_energy=f"{bip_avg_energy:.3f}",
                    pending_lease_ms=int(self._bip_pending_confirm_ms), confirmed_lease_ms=10500,
                    native_hero=bool(self._is_native_party_hero(int(target_agent_id))),
                    policy="stage2_32_effect_confirmation_plus_generation_bound_native_caster_receipts",
                )
            except Exception:
                pass
            return True

        self._bip_precast_recharge_stamp.pop(int(target_agent_id), None)
        self._bip_precast_hp_fraction.pop(int(target_agent_id), None)
        self._bip_expected_sacrifice_fraction.pop(int(target_agent_id), None)
        return False
    #endregion

    #region S
    def Signet_of_Agony(self) -> BuildCoroutine:
        """Self-cast Signet of Agony while a foe stands within "nearby" range.

        Signet of Agony deals damage to foes near the caster and self-inflicts
        Bleeding (which Contagion spreads). Cast only while the caster is not
        already bleeding, so it re-applies the self-Bleeding once it lapses; its
        recharge prevents spam.
        """
        signet_of_agony_id: int = Skill.GetID("Signet_of_Agony")

        if not self.build.IsSkillEquipped(signet_of_agony_id):
            return False
        if not Routines.Agents.GetNearestEnemy(Range.Nearby.value):
            return False
        if Agent.IsBleeding(Player.GetAgentID()):
            return False

        return (yield from self.build.CastSkillID(
            skill_id=signet_of_agony_id,
            log=False,
            aftercast_delay=250,
        ))
    #endregion
