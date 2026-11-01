from dataclasses import dataclass
from collections.abc import Generator

from Py4GWCoreLib import Agent, AgentArray, Player, Profession, Range, Routines, BuildMgr, GLOBAL_CACHE
from Py4GWCoreLib.Skill import Skill
from Py4GWCoreLib.Builds.Any.HeroAI import HeroAI as HeroAIBuild
from Py4GWCoreLib.Builds.Skills import SkillsTemplate
from Py4GWCoreLib.Builds.Skills import MesmerwayCoordination as MWC
from Py4GWCoreLib.Builds.Skills import MesmerWeaponSetController as MWSC
from Py4GWCoreLib.Builds.Skills import PIMesmerCoordination as PIC


Ineptitude_ID = Skill.GetID("Ineptitude")
Psychic_Instability_ID = Skill.GetID("Psychic_Instability")
Wandering_Eye_ID = Skill.GetID("Wandering_Eye")
Clumsiness_ID = Skill.GetID("Clumsiness")
Signet_of_Clumsiness_ID = Skill.GetID("Signet_of_Clumsiness")
Technobabble_ID = Skill.GetID("Technobabble")
Power_Drain_ID = Skill.GetID("Power_Drain")
Arcane_Conundrum_ID = Skill.GetID("Arcane_Conundrum")
Frustration_ID = Skill.GetID("Frustration")
Fragility_ID = Skill.GetID("Fragility")
Web_of_Disruption_ID = Skill.GetID("Web_of_Disruption")
Phantom_Pain_ID = Skill.GetID("Phantom_Pain")
Epidemic_ID = Skill.GetID("Epidemic")
Ebon_Battle_Standard_of_Wisdom_ID = Skill.GetID("Ebon_Battle_Standard_of_Wisdom")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Winter_ID = Skill.GetID("Winter")
Mantra_of_Frost_ID = Skill.GetID("Mantra_of_Frost")
Drain_Enchantment_ID = Skill.GetID("Drain_Enchantment")
Accumulated_Pain_ID = Skill.GetID("Accumulated_Pain")


@dataclass(slots=True)
class _IneptitudeSnapshot:
    in_aggro: bool = False
    close_to_aggro: bool = False
    energy_pct: float = 1.0


class Ineptitude(BuildMgr):
    """HR Mesmerway anti-martial variant.

    Supports either Ineptitude or Psychic Instability as the elite while
    preserving the same Illusion anti-martial/support shell. Wandering Eye and
    Clumsiness identify the build; the elite choice is validated by ScoreMatch.
    PI uses a cross-account packet + exact-cast claim so it can take first refusal
    on strong clusters without causing duplicate interrupts from the eSurge Mesmers.
    """

    # Stage 1.72: PI is deliberately a spam-control elite when a real packet is
    # available.  Three foes in Nearby range is the normal "good cluster"
    # threshold.  During cleanup, role control may still use PI on a lone
    # healer/support/caster, and active martial pressure is always eligible.
    PI_GOOD_CLUSTER_MIN = 3

    def ScoreMatch(self, current_primary=None, current_secondary=None, current_skills=None) -> int:
        skills = list(current_skills) if current_skills is not None else self._get_current_skills()
        skill_set = {int(s) for s in skills if int(s or 0) > 0}
        if not ({int(Ineptitude_ID), int(Psychic_Instability_ID)} & skill_set):
            return -1
        score = int(super().ScoreMatch(
            current_primary=current_primary, current_secondary=current_secondary, current_skills=skills
        ))
        return int(score + 1) if score >= 0 else -1

    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Ineptitude - HR Mesmerway",
            required_primary=Profession.Mesmer,
            template_code="OQBDAawDSvAIg5ZkAAAAAAAAAA",
            required_skills=[Wandering_Eye_ID, Clumsiness_ID],
            optional_skills=[
                Ineptitude_ID,
                Psychic_Instability_ID,
                Signet_of_Clumsiness_ID,
                Technobabble_ID,
                Power_Drain_ID,
                Arcane_Conundrum_ID,
                Frustration_ID,
                Fragility_ID,
                Web_of_Disruption_ID,
                Phantom_Pain_ID,
                Epidemic_ID,
                Ebon_Battle_Standard_of_Wisdom_ID,
                Ebon_Vanguard_Assassin_Support_ID,
                Winter_ID,
                Mantra_of_Frost_ID,
                Accumulated_Pain_ID, Drain_Enchantment_ID,
            ],
        )
        if match_only:
            return
        self.SetFallback("HeroAI", HeroAIBuild(standalone_fallback=True))
        self.SetBlockedSkills([
            Ineptitude_ID, Psychic_Instability_ID, Wandering_Eye_ID, Clumsiness_ID,
            Signet_of_Clumsiness_ID, Technobabble_ID, Power_Drain_ID,
            Arcane_Conundrum_ID, Frustration_ID, Fragility_ID,
            Web_of_Disruption_ID, Phantom_Pain_ID, Epidemic_ID, Ebon_Battle_Standard_of_Wisdom_ID,
            Ebon_Vanguard_Assassin_Support_ID,
            Accumulated_Pain_ID,
            Winter_ID,
            Mantra_of_Frost_ID,
        ])
        self.SetSkillCastingFn(self._run_local_skill_logic)
        self.skills: SkillsTemplate = SkillsTemplate(self)
        self._combat_started_ms = 0
        self._was_in_aggro = False
        self._mesmerway_weapon_sets = MWSC.MesmerWeaponSetController(role="ineptitude")
        self._mesmerway_loadout_logged = False
        self._mesmerway_last_cast_evidence: str = "not_queued"
        self._mesmerway_last_queue_reject_reason: str = ""
        self._mesmerway_queue_reject_backoff: dict[tuple[int, int], tuple[int, str]] = {}
        self._mesmerway_global_queue_reject_until_ms: int = 0
        self._mesmerway_global_queue_reject_reason: str = ""
        self._mesmerway_phantom_diag_last_ms: int = 0

    @staticmethod
    def _now_ms() -> int:
        try:
            import PySystem
            return int(PySystem.get_tick_count64() or 0)
        except Exception:
            return 0

    def _queue_reject_backoff_active(self, skill_id: int, target_id: int) -> bool:
        key = (int(skill_id or 0), int(target_id or 0))
        row = self._mesmerway_queue_reject_backoff.get(key)
        if not row:
            return False
        until_ms, _reason = row
        now = int(self._now_ms() or 0)
        if now <= 0 or now >= int(until_ms or 0):
            self._mesmerway_queue_reject_backoff.pop(key, None)
            return False
        return True

    def _global_queue_reject_backoff_active(self) -> bool:
        now = int(self._now_ms() or 0)
        if now <= 0 or now >= int(self._mesmerway_global_queue_reject_until_ms or 0):
            self._mesmerway_global_queue_reject_until_ms = 0
            self._mesmerway_global_queue_reject_reason = ""
            return False
        return True

    def _remember_queue_reject(self, skill_id: int, target_id: int, reason: str) -> None:
        now = int(self._now_ms() or 0)
        if now <= 0:
            return
        reason = str(reason or "unknown_buildmgr_rejection")
        # Short per-skill/target cooldown: the same rejected selector result must
        # not be hammered every controller tick. Different targets remain free.
        self._mesmerway_queue_reject_backoff[(int(skill_id), int(target_id))] = (
            int(now + 700), reason
        )
        # Some preflight failures are controller-wide, not target-specific. If
        # Fragility just lost to local_cast_pending, immediately trying Phantom
        # Pain in the same pass can only hit the identical gate. Suppress only
        # that same scheduler condition; do not mask skill/target-specific gates.
        if reason in {"local_cast_pending", "map_not_explorable"}:
            self._mesmerway_global_queue_reject_until_ms = int(now + 300)
            self._mesmerway_global_queue_reject_reason = reason

    def _log_phantom_no_plan(self, reason: str, **fields: object) -> None:
        now = int(self._now_ms() or 0)
        if now > 0 and now - int(self._mesmerway_phantom_diag_last_ms or 0) < 5000:
            return
        self._mesmerway_phantom_diag_last_ms = now
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "INEPTITUDE_PHANTOM_PAIN_NO_PLAN",
                reason=str(reason or "unknown"),
                **fields,
                policy="stage1_56_selector_diagnostics_no_guard_relaxation",
            )
        except Exception:
            pass

    def _snapshot(self) -> _IneptitudeSnapshot:
        in_aggro = bool(self.IsInAggro())
        now = self._now_ms()
        elapsed = int(MWC.wisdom_combat_elapsed_ms(in_aggro) or 0)
        self._combat_started_ms = int(now - elapsed) if now > 0 and elapsed > 0 else 0
        self._was_in_aggro = in_aggro
        try:
            energy = float(Agent.GetEnergy(Player.GetAgentID()) or 0.0)
        except Exception:
            energy = 1.0
        return _IneptitudeSnapshot(in_aggro, bool(self.IsCloseToAggro()), max(0.0, min(1.0, energy)))

    @staticmethod
    def _effect_ids(agent_id: int) -> set[int]:
        try:
            return {int(getattr(e, "skill_id", 0) or 0) for e in (GLOBAL_CACHE.Effects.GetEffects(int(agent_id)) or [])}
        except Exception:
            return set()

    @staticmethod
    def _physical_profession(enemy_id: int) -> bool:
        """RC4-aware martial classifier with live weapon fallback.

        DoA/Urgoz logs showed Agent.GetProfessions() returning unusable values for
        many hostile NPCs.  We therefore merge live profession, EnemyKnowledge,
        OfflineKnowledge and the enemy's actual equipped weapon type.
        """
        aid = int(enemy_id or 0)
        if aid <= 0:
            return False

        martial_ids = {
            int(getattr(Profession.Warrior, "value", Profession.Warrior)),
            int(getattr(Profession.Ranger, "value", Profession.Ranger)),
            int(getattr(Profession.Assassin, "value", Profession.Assassin)),
            int(getattr(Profession.Dervish, "value", Profession.Dervish)),
            int(getattr(Profession.Paragon, "value", Profession.Paragon)),
        }

        # 1) Direct live profession when the client exposes it.
        try:
            primary, secondary = Agent.GetProfessions(aid)
            live = {
                int(getattr(primary, "value", primary) or 0),
                int(getattr(secondary, "value", secondary) or 0),
            }
            if live.intersection(martial_ids):
                return True
        except Exception:
            pass

        # 2) RC4 EnemyKnowledge retained profession/model context.
        rec = None
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            rec = EnemyKnowledge.observe(aid, 0)
            known = {int(getattr(rec, "primary", 0) or 0), int(getattr(rec, "secondary", 0) or 0)}
            if known.intersection(martial_ids):
                return True
        except Exception:
            rec = None

        # 3) Offline model profile can identify profession even when live hostile
        # profession data is unavailable on first contact.
        try:
            from Py4GWCoreLib.Builds.Knowledge import OfflineKnowledge
            if rec is None:
                from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
                rec = EnemyKnowledge.observe(aid, 0)
            profile = OfflineKnowledge.enemy_profile(
                int(getattr(rec, "model_id", 0) or 0),
                str(getattr(rec, "name", "") or ""),
                int(getattr(rec, "map_id", 0) or 0),
            )
            prof_names = {
                str(profile.get("primary_profession", "") or "").casefold(),
                str(profile.get("secondary_profession", "") or "").casefold(),
            }
            if prof_names.intersection({"warrior", "ranger", "assassin", "dervish", "paragon"}):
                return True
        except Exception:
            pass

        # 4) Actual martial weapon is an immediate practical signal for exactly
        # what Ineptitude/Wandering Eye/Clumsiness care about: attacks.
        try:
            weapon_type = int(Agent.GetWeaponType(aid)[0] or 0)
            if weapon_type in {1, 2, 3, 4, 5, 6, 7}:  # bow/axe/hammer/daggers/scythe/spear/sword
                return True
        except Exception:
            pass

        return False

    @staticmethod
    def _adjacent_attackers(enemy_id: int, members: list[int]) -> int:
        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils
            xy = Agent.GetXY(int(enemy_id))
            return sum(
                1 for eid in members
                if Agent.IsAlive(int(eid)) and Agent.IsAttacking(int(eid))
                and Utils.Distance(xy, Agent.GetXY(int(eid))) <= float(Range.Adjacent.value)
            )
        except Exception:
            return 0

    def _adjacent_martial_attackers(self, enemy_id: int, members: list[int]) -> int:
        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils
            xy = Agent.GetXY(int(enemy_id))
            return sum(
                1 for eid in members
                if Agent.IsAlive(int(eid)) and Agent.IsAttacking(int(eid))
                and self._physical_profession(int(eid))
                and Utils.Distance(xy, Agent.GetXY(int(eid))) <= float(Range.Adjacent.value)
            )
        except Exception:
            return 0

    @staticmethod
    def _nearby_enemy_density(enemy_id: int, members: list[int]) -> int:
        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils
            xy = Agent.GetXY(int(enemy_id))
            return sum(
                1 for eid in members
                if Agent.IsAlive(int(eid))
                and Utils.Distance(xy, Agent.GetXY(int(eid))) <= float(Range.Nearby.value)
            )
        except Exception:
            return 0

    @staticmethod
    def _st_pressure_rank(enemy_id: int) -> int:
        try:
            from Py4GWCoreLib.Builds.Skills import STDefensePriority
            return int(STDefensePriority.protection_pressure_rank(int(enemy_id)) or 0)
        except Exception:
            return 0

    @staticmethod
    def _reachable_enemy_ids() -> list[int]:
        try:
            enemies = AgentArray.GetEnemyArray() or []
            enemies = AgentArray.Filter.ByDistance(enemies, Player.GetXY(), float(Range.Spellcast.value))
            return [
                int(eid) for eid in enemies
                if int(eid or 0) > 0 and Agent.IsValid(int(eid)) and Agent.IsAlive(int(eid))
            ]
        except Exception:
            return []

    @staticmethod
    def _pi_role_scores(enemy_id: int) -> tuple[int, int, int]:
        """Return healer/support/caster scores with lightweight live fallback."""
        healer = support = caster = 0
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            scores = EnemyKnowledge.role_scores(int(enemy_id))
            healer = int(scores.get("healer", 0) or 0)
            support = int(scores.get("support", 0) or 0)
            caster = int(scores.get("caster", 0) or 0)
        except Exception:
            pass
        try:
            p, s = Agent.GetProfessions(int(enemy_id))
            profs = {int(getattr(p, "value", p) or 0), int(getattr(s, "value", s) or 0)}
            monk = int(getattr(Profession.Monk, "value", Profession.Monk))
            rit = int(getattr(Profession.Ritualist, "value", Profession.Ritualist))
            mesmer = int(getattr(Profession.Mesmer, "value", Profession.Mesmer))
            ele = int(getattr(Profession.Elementalist, "value", Profession.Elementalist))
            necro = int(getattr(Profession.Necromancer, "value", Profession.Necromancer))
            if monk in profs:
                healer = max(healer, 80)
            if rit in profs:
                support = max(support, 75)
            if profs.intersection({monk, rit, mesmer, ele, necro}):
                caster = max(caster, 60)
        except Exception:
            pass
        try:
            if Agent.IsCaster(int(enemy_id)):
                caster = max(caster, 55)
        except Exception:
            pass
        return int(healer), int(support), int(caster)

    @staticmethod
    def _pi_packet_members(target_id: int, enemy_ids: list[int]) -> list[int]:
        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils
            xy = Agent.GetXY(int(target_id))
            return sorted({
                int(eid) for eid in enemy_ids
                if Agent.IsValid(int(eid)) and Agent.IsAlive(int(eid))
                and Utils.Distance(xy, Agent.GetXY(int(eid))) <= float(Range.Nearby.value)
            })
        except Exception:
            return [int(target_id)] if int(target_id or 0) > 0 else []

    def _pi_ranked_interrupt_candidates(self) -> list[dict[str, object]]:
        """Rank live PI activations for spam control without moving damage focus.

        Normal combat: any feasible activation in a 3+ Nearby packet is eligible,
        with denser packets first. Active martial/ST pressure remains a fallback.
        Cleanup: control healers, then supporters, then casters even when the
        remaining packet is small; team damage focus itself stays untouched.
        """
        if not self.IsSkillEquipped(Psychic_Instability_ID) or not self.CanCastSkillID(Psychic_Instability_ID):
            return []
        try:
            from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
            plan = TeamCombatFocus.get_team_combat_plan(
                filter_range=Range.Spellcast.value, minimum_enemies=2, consumer_role="ineptitude_pi"
            )
            mode = str(plan.mode or "none")
            focus_target = int(plan.focus_target or 0)
            plan_members = {int(v) for v in tuple(plan.member_ids or ()) if int(v or 0) > 0}
        except Exception:
            mode = "none"
            focus_target = 0
            _anchor, members = MWC.cluster_anchor_and_members(consumer_role="ineptitude_pi")
            plan_members = {int(v) for v in members}

        cleanup_mode = mode in {"cleanup", "low_hp_finish"}
        enemies = self._reachable_enemy_ids()
        rows: list[tuple[tuple[int, ...], dict[str, object]]] = []
        for eid in enemies:
            try:
                enemy_skill = int(Agent.GetCastingSkillID(int(eid)) or 0)
            except Exception:
                enemy_skill = 0
            if enemy_skill <= 0:
                continue
            try:
                from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import interrupt_is_feasible
                if not bool(interrupt_is_feasible(int(eid), int(Psychic_Instability_ID))):
                    continue
            except Exception:
                pass
            packet = self._pi_packet_members(int(eid), enemies)
            density = int(len(packet))
            good_cluster = density >= int(self.PI_GOOD_CLUSTER_MIN)
            physical = bool(self._physical_profession(int(eid)))
            try:
                attacking = bool(Agent.IsAttacking(int(eid)))
            except Exception:
                attacking = False
            st_pressure = int(self._st_pressure_rank(int(eid)) or 0)
            healer, support, caster = self._pi_role_scores(int(eid))
            if healer >= 70:
                role_rank = 3
            elif support >= 70:
                role_rank = 2
            elif caster >= 50:
                role_rank = 1
            else:
                role_rank = 0

            in_focus_packet = int(eid) in plan_members if plan_members else False
            physical_pressure = bool(physical and attacking)
            # Do not spend PI on random scattered filler.  Good clusters always
            # qualify; cleanup role control and active physical/ST pressure are
            # the deliberate small-packet fallbacks.
            if not (good_cluster or (cleanup_mode and role_rank > 0) or physical_pressure or st_pressure > 0):
                continue
            if not cleanup_mode and plan_members and not in_focus_packet and not (physical_pressure or st_pressure > 0):
                continue

            if good_cluster:
                # Cluster spam wins before role/danger semantics. Within equally
                # dense packets, prefer ST/martial pressure then healer/support.
                rank = (
                    3, density, int(st_pressure), int(physical_pressure), role_rank,
                    int(in_focus_packet), int(eid == focus_target), -int(eid),
                )
            elif cleanup_mode:
                # Explicit requested cleanup order: healer -> supporter -> caster.
                rank = (
                    2, role_rank, int(eid == focus_target), int(st_pressure),
                    int(physical_pressure), density, int(in_focus_packet), -int(eid),
                )
            else:
                rank = (
                    1, int(st_pressure), int(physical_pressure), density, role_rank,
                    int(in_focus_packet), int(eid == focus_target), -int(eid),
                )
            rows.append((rank, {
                "target_id": int(eid), "enemy_skill_id": int(enemy_skill),
                "packet": tuple(packet), "density": int(density),
                "good_cluster": bool(good_cluster), "cleanup_mode": bool(cleanup_mode),
                "role_rank": int(role_rank), "healer": int(healer),
                "support": int(support), "caster": int(caster),
                "physical": bool(physical), "attacking": bool(attacking),
                "st_pressure": int(st_pressure), "focus_target": int(focus_target),
            }))
        rows.sort(key=lambda row: row[0], reverse=True)
        return [dict(row[1]) for row in rows]

    def _cast_psychic_instability(self) -> Generator[object, None, bool]:
        """Spam PI on valuable clusters with cross-account first refusal."""
        candidates = self._pi_ranked_interrupt_candidates()
        for row in candidates:
            target = int(row.get("target_id", 0) or 0)
            enemy_skill = int(row.get("enemy_skill_id", 0) or 0)
            packet = tuple(int(v) for v in tuple(row.get("packet", ()) or ()) if int(v or 0) > 0)
            if target <= 0 or enemy_skill <= 0 or not packet:
                continue
            if not PIC.reserve_pi_packet(packet, duration_ms=900):
                continue
            allow_override = bool(row.get("good_cluster", False))
            if not MWC.reserve_priority_pi_interrupt_target(
                target, allow_protected_override=allow_override
            ):
                PIC.release_pi_packet(packet, reason="exact_cast_claim_unavailable")
                continue
            did = yield from self._cast_targeted(
                Psychic_Instability_ID, target, aftercast=180, urgent=True, interrupt_claim=True
            )
            if not did:
                MWC.release_single_interrupt_reservation(
                    target, Psychic_Instability_ID, reason="ineptitude_pi_not_fired"
                )
                PIC.release_pi_packet(packet, reason="pi_not_fired")
                continue
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "INEPTITUDE_PI_CAST", target_id=int(target), enemy_skill_id=int(enemy_skill),
                    packet_size=int(len(packet)), packet_members=",".join(str(v) for v in packet),
                    good_cluster=bool(row.get("good_cluster", False)),
                    cleanup_mode=bool(row.get("cleanup_mode", False)),
                    healer_score=int(row.get("healer", 0) or 0),
                    support_score=int(row.get("support", 0) or 0),
                    caster_score=int(row.get("caster", 0) or 0),
                    physical=bool(row.get("physical", False)), attacking=bool(row.get("attacking", False)),
                    st_pressure=int(row.get("st_pressure", 0) or 0),
                    policy="stage1_72_pi_spam_cluster_first_cleanup_healer_support_caster_exact_claim",
                )
            except Exception:
                pass
            # Keep the short PI packet lease alive after success. It expires on
            # its own while the knockdown is occurring and keeps other Mesmers
            # from racing a redundant interrupt into the same packet.
            return True
        return False

    def _pick_illusion_attack_target(
        self,
        skill_id: int,
        *,
        require_attacking: bool = False,
        require_physical: bool = False,
        urgent: bool = False,
    ) -> int:
        _anchor, members = MWC.cluster_anchor_and_members()
        rows = []
        for eid in members:
            eid = int(eid)
            try:
                if not Agent.IsValid(eid) or not Agent.IsAlive(eid):
                    continue
                attacking = bool(Agent.IsAttacking(eid))
            except Exception:
                continue
            if require_attacking and not attacking:
                continue
            # A pending/active Fragility feeder packet is deliberately reserved
            # for Shatter. Do not put a newer Ineptitude-family hex on top.
            if not urgent and skill_id != Signet_of_Clumsiness_ID and MWC.is_fragility_feeder_window(eid):
                continue
            if int(skill_id) in self._effect_ids(eid):
                continue
            physical = int(self._physical_profession(eid))
            # A target already hexed by our Frustration can trigger damage on
            # an interrupted *attack* even when monster profession/weapon data
            # does not classify it as physical. Do not relax Ineptitude itself.
            frustration_combo = bool(
                int(skill_id) in (int(Wandering_Eye_ID), int(Clumsiness_ID))
                and attacking and int(Frustration_ID) in self._effect_ids(eid)
            )
            if require_physical and not (physical or frustration_combo):
                continue
            st_pressure = int(self._st_pressure_rank(eid))
            adjacent_attackers = self._adjacent_attackers(eid, members)
            adjacent_martial_attackers = self._adjacent_martial_attackers(eid, members)
            nearby_density = self._nearby_enemy_density(eid, members)
            try:
                hp = float(Agent.GetHealth(eid) or 1.0)
            except Exception:
                hp = 1.0
            # Stage 1.71 skill-semantics ranking:
            # - hard/watch pressure on ST/core spirits wins locally without
            #   forcing the whole team off its packet;
            # - active martial attackers beat idle martial pre-setup;
            # - Wandering Eye wants a triggering attacker in the densest Nearby
            #   packet because its damage splashes Nearby, while Ineptitude /
            #   Clumsiness / Signet prefer adjacent martial attackers.
            if int(skill_id) == int(Wandering_Eye_ID):
                rows.append((
                    int(st_pressure > 0 and physical), int(st_pressure),
                    int(frustration_combo), int(attacking),
                    int(nearby_density), int(adjacent_martial_attackers), physical,
                    -hp, -eid, eid,
                ))
            else:
                rows.append((
                    int(st_pressure > 0 and physical), int(st_pressure),
                    int(frustration_combo), int(attacking),
                    int(adjacent_martial_attackers), int(adjacent_attackers), physical,
                    -hp, -eid, eid,
                ))
        if not rows:
            return 0
        rows.sort(reverse=True)
        return int(rows[0][-1])

    def _pick_arcane_conundrum_target(self) -> int:
        _anchor, members = MWC.cluster_anchor_and_members()
        rows = []
        try:
            from Py4GWCoreLib.Py4GWcorelib import Utils
        except Exception:
            Utils = None
        for eid in members:
            eid = int(eid)
            effects = self._effect_ids(eid)
            if (
                int(Arcane_Conundrum_ID) in effects
                or int(Frustration_ID) in effects
                or MWC.is_mistrust_protected(eid)
                or MWC.is_fragility_feeder_window(eid)
                or MWC.is_optional_shatter_pipeline_pending(eid)
            ):
                continue
            try:
                from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
                scores = EnemyKnowledge.role_scores(eid)
                caster = int(scores.get("caster", 0))
                healer = int(scores.get("healer", 0))
                support = int(scores.get("support", 0))
            except Exception:
                caster = healer = support = 0
            if max(caster, healer, support) < 50:
                try:
                    if not Agent.IsCaster(eid):
                        continue
                except Exception:
                    continue

            affected = [eid]
            if Utils is not None:
                try:
                    cxy = Agent.GetXY(eid)
                    affected = [
                        int(other) for other in members
                        if Agent.IsAlive(int(other))
                        and Utils.Distance(cxy, Agent.GetXY(int(other))) <= float(Range.Adjacent.value)
                    ]
                except Exception:
                    affected = [eid]

            # Arcane Conundrum also hexes adjacent foes. Do not accidentally
            # cover a reserved Shatter feeder, and rank the centre by how many
            # fresh relevant casters the AoE actually slows.
            if any(
                MWC.is_fragility_feeder_window(int(other))
                or MWC.is_optional_shatter_pipeline_pending(int(other))
                for other in affected
            ):
                continue

            fresh_relevant = 0
            healer_sum = support_sum = caster_sum = casting_count = 0
            for other in affected:
                other = int(other)
                other_effects = self._effect_ids(other)
                if int(Arcane_Conundrum_ID) in other_effects or int(Frustration_ID) in other_effects:
                    continue
                if MWC.is_mistrust_protected(other):
                    continue
                try:
                    from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
                    role = EnemyKnowledge.role_scores(other)
                    oc = int(role.get("caster", 0))
                    oh = int(role.get("healer", 0))
                    os = int(role.get("support", 0))
                except Exception:
                    oc = oh = os = 0
                try:
                    relevant = max(oc, oh, os) >= 50 or bool(Agent.IsCaster(other))
                except Exception:
                    relevant = max(oc, oh, os) >= 50
                if not relevant:
                    continue
                fresh_relevant += 1
                healer_sum += oh
                support_sum += os
                caster_sum += oc
                try:
                    casting_count += 1 if Agent.IsCasting(other) else 0
                except Exception:
                    pass

            if fresh_relevant <= 0:
                continue
            rows.append((
                int(fresh_relevant), int(healer_sum), int(support_sum), int(caster_sum),
                int(casting_count), int(healer), int(support), int(caster), -eid, eid,
            ))
        rows.sort(reverse=True)
        return int(rows[0][-1]) if rows else 0

    def _log_illusion_cast(self, skill_id: int, target_id: int, label: str) -> None:
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "INEPTITUDE_CORE_CAST",
                skill_id=int(skill_id), target_id=int(target_id), label=str(label),
                target_attacking=bool(Agent.IsAttacking(int(target_id))),
                target_physical=bool(self._physical_profession(int(target_id))),
                policy="same_authoritative_packet_martial_attacker_priority",
            )
        except Exception:
            pass

    def _cast_targeted(
        self, skill_id: int, target_id: int, *, aftercast: int = 250,
        urgent: bool = False, interrupt_claim: bool = False,
        epidemic_plan: dict[str, object] | None = None,
    ) -> Generator[object, None, bool]:
        if (
            int(target_id or 0) <= 0
            or not self.IsSkillEquipped(int(skill_id))
            or not self.CanCastSkillID(int(skill_id))
        ):
            return False
        options: MWC.InterruptDispatchOptions = {}
        if interrupt_claim or int(skill_id) == Power_Drain_ID:
            options = MWC.interrupt_dispatch_options(target_id, skill_id)
        receipt, options = MWC.coordinated_dispatch_options(skill_id, target_id, options, urgent=urgent)
        if int(skill_id) == int(Epidemic_ID) and epidemic_plan:
            receipt.epidemic_plan = dict(epidemic_plan)
        accepted = bool(
            (
                yield from self.CastSkillIDAndRestoreTarget(
                    skill_id=int(skill_id),
                    target_agent_id=int(target_id),
                    log=False,
                    aftercast_delay=int(aftercast),
                    **options,
                )
            )
        )
        if not accepted:
            receipt.queue_reject_reason = str(self.GetLastCastRejectReason() or "unknown_buildmgr_rejection")
            self._mesmerway_last_queue_reject_reason = str(receipt.queue_reject_reason)
            self._remember_queue_reject(int(skill_id), int(target_id), receipt.queue_reject_reason)
        confirmed = yield from MWC.confirm_coordinated_cast(receipt, accepted)
        self._mesmerway_last_cast_evidence = str(receipt.evidence)
        return bool(confirmed)

    def _cast_power_drain(self, max_energy_pct: float, *, tactical_only: bool = False, critical_only: bool = False) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Power_Drain_ID) or not self.CanCastSkillID(Power_Drain_ID):
            return False
        try:
            energy = float(Agent.GetEnergy(Player.GetAgentID()) or 0.0)
        except Exception:
            energy = 1.0
        if energy > float(max_energy_pct):
            return False
        if tactical_only:
            picker = getattr(MWC, 'reserve_tactical_power_drain_target', None)
            target = int(picker(critical_only=bool(critical_only)) or 0) if callable(picker) else 0
        else:
            target = int(MWC.reserve_single_interrupt_target(Power_Drain_ID) or 0)
        if target <= 0:
            return False
        did = yield from self._cast_targeted(Power_Drain_ID, target)
        if not did:
            MWC.release_single_interrupt_reservation(target, Power_Drain_ID, reason="ineptitude_power_drain_not_fired")
        return bool(did)

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
                    "MESMERWAY_MANTRA_OF_FROST", role="pi", energy=round(float(energy), 3),
                    policy="stage2_02_pi_after_primary_control",
                )
            except Exception:
                pass
        return bool(did)

    def _cast_winter_backup(self) -> Generator[object, None, bool]:
        """PI is only the fallback Winter carrier; a ready ST always gets priority."""
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
            MWC.release_winter_pending(str(self.GetLastCastRejectReason() or "pi_winter_not_fired"))
            return False
        record = MWC.register_winter_cast(role="pi_backup", cast_xy=winter_cast_xy)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_WINTER_CAST", role="pi_backup",
                duration_ms=int(record.get("duration_ms", 0) or 0),
                policy="stage2_02_st_primary_pi_backup_handoff",
            )
        except Exception:
            pass
        return True

    def _cast_evas(self, *, min_energy_pct: float = 0.45) -> Generator[object, None, bool]:
        """Low-overhead damage filler for the Stage 2.00 speed rebase.

        EVAS is deliberately below PI, Frustration and the core anti-martial
        package.  It adds one summon/knockdown body without introducing another
        setup pipeline such as Ether Nightmare.  The existing shared target
        lease spreads concurrent EVAS copies across useful live foes.
        """
        if not self.IsSkillEquipped(Ebon_Vanguard_Assassin_Support_ID):
            return False
        if not self.CanCastSkillID(Ebon_Vanguard_Assassin_Support_ID):
            return False
        try:
            energy = float(Agent.GetEnergy(Player.GetAgentID()) or 0.0)
        except Exception:
            energy = 1.0
        if energy < float(min_energy_pct):
            return False
        target = int(MWC.reserve_evas_target() or 0)
        if target <= 0:
            return False
        did = yield from self._cast_targeted(Ebon_Vanguard_Assassin_Support_ID, target, aftercast=250)
        if did:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "INEPTITUDE_EVAS_DISTRIBUTED", target_id=int(target),
                    policy="stage2_00_172_speed_rebase_low_overhead_filler",
                )
            except Exception:
                pass
        return bool(did)

    def _cast_technobabble(self) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Technobabble_ID) or not self.CanCastSkillID(Technobabble_ID):
            return False
        target = int(MWC.reserve_technobabble_target() or 0)
        if target <= 0:
            return False
        did = yield from self._cast_targeted(Technobabble_ID, target)
        if did:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event("INEPTITUDE_TECHNOBABBLE", target_id=target,
                                      policy="exact_packet_no_mistrust_overlap_fresh_daze")
            except Exception:
                pass
        return bool(did)

    def _cast_accumulated_cleanup(self) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Accumulated_Pain_ID) or not self.CanCastSkillID(Accumulated_Pain_ID):
            return False
        target = int(MWC.reserve_cleanup_target(hard_only=True) or 0)
        if target <= 0:
            return False
        try:
            effects = self._effect_ids(int(target))
            hex_count = sum(1 for sid in effects if GLOBAL_CACHE.Skill.Flags.IsHex(int(sid)))
        except Exception:
            hex_count = 0
        if hex_count < 2:
            MWC.release_cleanup_reservation(target, reason="inept_cleanup_needs_two_hexes")
            return False
        casted = bool((yield from self._cast_targeted(Accumulated_Pain_ID, target, aftercast=180)))
        if not casted:
            MWC.release_cleanup_reservation(target, reason="inept_cleanup_cast_not_fired")
        return casted

    def _cast_accumulated_offense(self) -> Generator[object, None, bool]:
        """Keep the equipped direct hex payoff active outside hard cleanup.

        No new hex is cast to prepare it. Control, active martial punishment
        and >=5% HP kill-focus skills retain their established priority.
        """
        if not self.IsSkillEquipped(Accumulated_Pain_ID) or not self.CanCastSkillID(Accumulated_Pain_ID):
            return False
        try:
            from Py4GWCoreLib.Builds.Skills import TeamCombatFocus
            focus = int(TeamCombatFocus.get_team_primary_target(
                filter_range=float(Range.Spellcast.value), minimum_enemies=1,
                consumer_role="inept_accumulated_damage") or 0)
        except Exception:
            focus = 0
        if focus <= 0:
            return False
        try:
            effect_ids = self._effect_ids(focus)
            if sum(1 for sid in effect_ids if GLOBAL_CACHE.Skill.Flags.IsHex(int(sid))) < 2:
                return False
            if float(Agent.GetHealth(focus) or 0.0) <= 0.0:
                return False
        except Exception:
            return False
        return bool((yield from self._cast_targeted(Accumulated_Pain_ID, focus, aftercast=200)))

    def _cast_frustration(self) -> Generator[object, None, bool]:
        """Prioritize healers/support/casters we are interrupting.

        The shared selector keeps attacking physical targets as a fallback;
        Clumsiness and Wandering Eye can also trigger Frustration damage.
        """
        if not self.IsSkillEquipped(Frustration_ID) or not self.CanCastSkillID(Frustration_ID):
            return False
        target = int(MWC.reserve_frustration_target() or 0)
        if target <= 0:
            return False
        did = yield from self._cast_targeted(Frustration_ID, target)
        if did:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                enemy_skill_id = int(Agent.GetCastingSkillID(target) or 0) if Agent.IsCasting(target) else 0
                martial = bool(MWC.is_martial_attack_target(int(target)))
                attacking = bool(Agent.IsAttacking(int(target)))
                CombatDebug.log_event(
                    "INEPTITUDE_FRUSTRATION_CAST",
                    target_id=int(target),
                    enemy_skill_id=int(enemy_skill_id),
                    martial_target=bool(martial),
                    target_attacking=bool(attacking),
                    policy="stage2_59_healer_support_caster_first_attack_combo_fallback",
                )
            except Exception:
                pass
        return bool(did)

    def _cast_fragility(self) -> Generator[object, None, bool]:
        """One optional AoE condition-damage setup for the anti-martial Mesmer."""
        if self._global_queue_reject_backoff_active():
            return False
        if not self.IsSkillEquipped(Fragility_ID) or not self.CanCastSkillID(Fragility_ID):
            return False
        target = int(MWC.reserve_fragility_target() or 0)
        if target <= 0:
            return False
        if self._queue_reject_backoff_active(Fragility_ID, target):
            MWC.release_fragility_reservation(target, reason="local_same_rejection_backoff")
            return False
        did = yield from self._cast_targeted(Fragility_ID, target)
        if did:
            # Queue admission is not effect proof. Native/effect reconciliation
            # remains asynchronous and owns feeder publication.
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "INEPTITUDE_FRAGILITY_QUEUED",
                    target_id=int(target),
                    policy="stage1_55_queue_admission_not_cast_completion_effect_confirmation_async",
                )
            except Exception:
                pass
        else:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "INEPTITUDE_FRAGILITY_REJECTED", target_id=int(target),
                    evidence=str(self._mesmerway_last_cast_evidence),
                    queue_reject_reason=str(self._mesmerway_last_queue_reject_reason or ""),
                    reservation_released=True,
                    policy="stage1_55_failed_preparation_not_logged_as_cast",
                )
            except Exception:
                pass
        return bool(did)

    def _cast_web_interrupt(self, *, emergency_only: bool = True) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Web_of_Disruption_ID) or not self.CanCastSkillID(Web_of_Disruption_ID):
            return False
        target = int(MWC.reserve_single_interrupt_target(Web_of_Disruption_ID, emergency_only=bool(emergency_only)) or 0)
        if target <= 0:
            return False
        snapshot = MWC.interrupt_dispatch_snapshot(target, Web_of_Disruption_ID)
        did = yield from self._cast_targeted(
            Web_of_Disruption_ID, target, aftercast=120, urgent=True, interrupt_claim=True
        )
        if not did:
            MWC.release_single_interrupt_reservation(target, Web_of_Disruption_ID, reason="ineptitude_web_interrupt_not_fired")
        MWC.release_optional_setup_reservation(Web_of_Disruption_ID, target)
        if did:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "INEPTITUDE_WEB_CAST", target_id=target, mode="urgent_interrupt",
                    enemy_skill_id=int(snapshot.get("enemy_skill_id", 0) or 0),
                    hex_confirmed=True, first_interrupt_success_assumed=False,
                    policy="stage1_45_interrupt_attempt_plus_confirmed_hex",
                )
            except Exception:
                pass
        return bool(did)

    def _cast_web_setup(self) -> Generator[object, None, bool]:
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
                "INEPTITUDE_WEB_CAST", target_id=target, mode="shatter_setup", density=int(density),
                expected_shatter_packet_damage=round(float(packet_damage), 1), hex_confirmed=bool(did),
                abort_reason="" if did else str(self._mesmerway_last_cast_evidence),
                selection_reason="dense_clean_target_with_ready_shatter",
                policy="stage1_45_late_setup_no_second_interrupt_wait",
            )
        except Exception:
            pass
        return bool(did)

    def _cast_phantom_pain(self) -> Generator[object, None, bool]:
        if self._global_queue_reject_backoff_active():
            self._log_phantom_no_plan(
                "global_queue_backoff",
                queue_reject_reason=str(self._mesmerway_global_queue_reject_reason or ""),
            )
            return False
        if not self.IsSkillEquipped(Phantom_Pain_ID):
            return False
        if not self.CanCastSkillID(Phantom_Pain_ID):
            self._log_phantom_no_plan(
                "preflight_not_castable",
                queue_reject_reason=str(self.GetLastCastRejectReason() or "unknown_preflight_rejection"),
            )
            return False
        target = int(MWC.reserve_phantom_pain_target() or 0)
        if target <= 0:
            diag = dict(MWC.diagnose_phantom_pain_target_selection() or {})
            self._log_phantom_no_plan(
                str(diag.pop("reason", "no_selector_target")),
                **diag,
            )
            return False
        try:
            hp = max(0.0, float(Agent.GetHealth(target) or 0.0))
        except Exception:
            hp = -1.0
        if self._queue_reject_backoff_active(Phantom_Pain_ID, target):
            MWC.release_optional_setup_reservation(Phantom_Pain_ID, target)
            self._log_phantom_no_plan("target_queue_reject_backoff", target_id=int(target))
            return False
        did = yield from self._cast_targeted(Phantom_Pain_ID, target, aftercast=180)
        MWC.release_optional_setup_reservation(Phantom_Pain_ID, target)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            if did:
                CombatDebug.log_event(
                    "INEPTITUDE_PHANTOM_PAIN_QUEUED", target_id=target,
                    target_hp_ratio=round(float(hp), 3),
                    selection_reason="durable_or_important_clean_target_with_ready_shatter",
                    policy="stage1_55_queue_admission_waits_for_async_hex_proof",
                )
            else:
                CombatDebug.log_event(
                    "INEPTITUDE_PHANTOM_PAIN_REJECTED", target_id=target,
                    target_hp_ratio=round(float(hp), 3),
                    evidence=str(self._mesmerway_last_cast_evidence),
                    queue_reject_reason=str(self._mesmerway_last_queue_reject_reason or ""),
                    selection_reason="durable_or_important_clean_target_with_ready_shatter",
                    policy="stage1_55_failed_attempt_is_not_logged_as_cast",
                )
        except Exception:
            pass
        return bool(did)

    def _cast_epidemic(self) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Epidemic_ID) or not self.CanCastSkillID(Epidemic_ID):
            return False
        plan = MWC.reserve_epidemic_target()
        target = int(plan.get("source_id", 0) or 0)
        if target <= 0:
            return False
        if not MWC.epidemic_plan_still_valid(plan):
            MWC.release_epidemic_reservation(plan, reason="stale_before_dispatch")
            return False
        did = yield from self._cast_targeted(Epidemic_ID, target, aftercast=100, epidemic_plan=plan)
        if not did:
            MWC.release_epidemic_reservation(plan, reason=str(self._mesmerway_last_cast_evidence or "cast_unconfirmed"))
            return False
        # Queue admission only; native FINISHED receipt reconciliation observes
        # recipients and publishes served locks.
        return True

    def _cast_wisdom_coordinated(self) -> Generator[object, None, bool]:
        if not self.IsSkillEquipped(Ebon_Battle_Standard_of_Wisdom_ID):
            return False
        if not self.CanCastSkillID(Ebon_Battle_Standard_of_Wisdom_ID):
            return False
        elapsed = int(MWC.wisdom_combat_elapsed_ms(bool(self.IsInAggro())) or 0)
        caster_id = int(Player.GetAgentID() or 0)
        plan = MWC.wisdom_cast_plan(caster_id, elapsed, carrier_role="ineptitude")
        MWC.log_wisdom_plan(plan, role="ineptitude")
        if not bool(plan.get("allowed", False)):
            return False
        if not MWC.reserve_wisdom_cast(caster_id):
            return False

        dispatch_tick = self._now_ms()
        did = yield from self.CastSkillID(
            skill_id=Ebon_Battle_Standard_of_Wisdom_ID,
            target_agent_id=0,
            log=False,
            aftercast_delay=750,
        )
        if not did:
            MWC.release_wisdom_pending("dispatch_failed")
            return False
        confirmed, confirmation = yield from MWC.confirm_local_wisdom_activation(dispatch_tick)
        if not confirmed:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "MESMERWAY_WISDOM_CAST_FAILED", role="ineptitude", reason=str(confirmation),
                    selected_reason=str(plan.get("reason", "")), elapsed_ms=int(elapsed),
                )
            except Exception:
                pass
            return False
        record = MWC.register_wisdom_cast(caster_id=caster_id)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "MESMERWAY_WISDOM_CAST",
                role="ineptitude", elapsed_ms=int(elapsed), plan_reason=str(plan.get("reason", "")),
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

    def _cast_urgent_anti_martial(self) -> Generator[object, None, bool]:
        """Immediate physical protection wins over placing a 1 s ground ward."""
        for sid, label in (
            (Ineptitude_ID, "Ineptitude"),
            (Wandering_Eye_ID, "Wandering_Eye"),
            (Clumsiness_ID, "Clumsiness"),
        ):
            if not self.IsSkillEquipped(sid) or not self.CanCastSkillID(sid):
                continue
            target = self._pick_illusion_attack_target(sid, require_attacking=True, require_physical=True, urgent=True)
            if target and (yield from self._cast_targeted(sid, target, urgent=True)):
                self._log_illusion_cast(sid, target, label)
                return True
        return False

    def _log_loadout_once(self) -> None:
        if self._mesmerway_loadout_logged:
            return
        self._mesmerway_loadout_logged = True
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "INEPTITUDE_LOADOUT",
                frustration_equipped=bool(self.IsSkillEquipped(Frustration_ID)),
                psychic_instability_equipped=bool(self.IsSkillEquipped(Psychic_Instability_ID)),
                ineptitude_equipped=bool(self.IsSkillEquipped(Ineptitude_ID)),
                technobabble_equipped=bool(self.IsSkillEquipped(Technobabble_ID)),
                arcane_conundrum_equipped=bool(self.IsSkillEquipped(Arcane_Conundrum_ID)),
                fragility_equipped=bool(self.IsSkillEquipped(Fragility_ID)),
                web_equipped=bool(self.IsSkillEquipped(Web_of_Disruption_ID)),
                phantom_pain_equipped=bool(self.IsSkillEquipped(Phantom_Pain_ID)),
                epidemic_equipped=bool(self.IsSkillEquipped(Epidemic_ID)),
                wisdom_equipped=bool(self.IsSkillEquipped(Ebon_Battle_Standard_of_Wisdom_ID)),
                evas_equipped=bool(self.IsSkillEquipped(Ebon_Vanguard_Assassin_Support_ID)),
                winter_equipped=bool(self.IsSkillEquipped(Winter_ID)),
                mantra_of_frost_equipped=bool(self.IsSkillEquipped(Mantra_of_Frost_ID)),
                policy="stage2_02_pi_winter_backup_mantra_visibility",
            )
        except Exception:
            pass

    def _run_local_skill_logic(self) -> Generator[object, None, bool | None]:
        # Stage 1.47: reconcile previously queued casts only on later ticks.
        # Never wait inside the same controller step for the ACTION queue that
        # this step itself just populated.
        MWC.observe_pending_cast_receipts()
        if not Routines.Checks.Skills.CanCast():
            yield from Routines.Yield.wait(100)
            return False
        snap = self._snapshot()
        self._log_loadout_once()
        MWC.observe_wisdom_activity()

        if (yield from self._mesmerway_weapon_sets.maybe_switch(
            snap.energy_pct, in_combat=snap.in_aggro
        )):
            return True

        if not snap.in_aggro:
            if snap.close_to_aggro and (yield from self._cast_winter_backup()):
                return True
            if (yield from self._cast_mantra_of_frost(min_energy_pct=0.30)):
                return True
            return False

        # Stage 1.72: when PI is the equipped elite, it gets first refusal on
        # every worthwhile live activation. Good clusters are spam-control, not
        # scarce-danger-only interrupts; cleanup and active martial/ST pressure
        # provide the small-packet fallbacks.
        if (yield from self._cast_psychic_instability()):
            return True

        if snap.energy_pct <= 0.45 and (yield from self._cast_power_drain(0.45)):
            return True

        # 1/4-second attack interrupt/KD first when a real attack is in flight.
        target = self._pick_illusion_attack_target(
            Signet_of_Clumsiness_ID, require_attacking=True, require_physical=True
        )
        if target and (yield from self._cast_targeted(Signet_of_Clumsiness_ID, target, aftercast=180)):
            return True

        # Immediate anti-martial protection is allowed to beat the 1 s Wisdom
        # plant. Otherwise Wisdom is placed early, before the normal damage /
        # shutdown rotation, once the shared coverage/energy/fight plan says it
        # will actually pay off.
        if (yield from self._cast_urgent_anti_martial()):
            return True

        # Stage 2.59-PD experiment: exploit live high-value healer/support
        # casts while preserving immediate anti-martial and emergency actions.
        if (snap.energy_pct > 0.45 and
                (yield from self._cast_power_drain(
                    1.0, tactical_only=True, critical_only=bool(snap.energy_pct > 0.85),
                ))):
            return True
        # Web may fill a genuine emergency-interrupt gap, but never beats the
        # Ineptitude Mesmer's immediate anti-martial protection above.
        if (yield from self._cast_web_interrupt(emergency_only=True)):
            return True
        if (yield from self._cast_wisdom_coordinated()):
            return True

        if (yield from self._cast_accumulated_cleanup()):
            return True

        # Frustration remains the early caster setup after the ward opportunity.
        if (yield from self._cast_frustration()):
            return True

        # Core anti-martial packet. Do not dump these three attack punishers on
        # generic casters; that was one reason Frustration never received a turn.
        target = self._pick_illusion_attack_target(Ineptitude_ID, require_physical=True)
        if target and (yield from self._cast_targeted(Ineptitude_ID, target)):
            self._log_illusion_cast(Ineptitude_ID, target, "Ineptitude")
            return True
        target = self._pick_illusion_attack_target(Wandering_Eye_ID, require_physical=True)
        if target and (yield from self._cast_targeted(Wandering_Eye_ID, target)):
            self._log_illusion_cast(Wandering_Eye_ID, target, "Wandering_Eye")
            return True
        target = self._pick_illusion_attack_target(Clumsiness_ID, require_physical=True)
        if target and (yield from self._cast_targeted(Clumsiness_ID, target)):
            self._log_illusion_cast(Clumsiness_ID, target, "Clumsiness")
            return True

        # Caster shutdown remains coordinated with the Domination Mesmers.
        if (yield from self._cast_technobabble()):
            return True

        # Stage 2.02 Urgoz Frost package. ST owns Winter first refusal; PI plants
        # only when the shared spatial lease is expiring/out of range and no
        # ready ST carrier can do it. Mantra never outranks PI/core shutdown.
        if (yield from self._cast_winter_backup()):
            return True
        if (yield from self._cast_mantra_of_frost(min_energy_pct=0.40)):
            return True

        # If no martial attacker needed the fast signet above, it may still be
        # used as a low-priority attack interrupt/damage packet on another live
        # attacker. This must not steal the early anti-martial or caster-control
        # windows merely because a wanding caster happens to be auto-attacking.
        target = self._pick_illusion_attack_target(Signet_of_Clumsiness_ID, require_attacking=True)
        if target and (yield from self._cast_targeted(Signet_of_Clumsiness_ID, target, aftercast=180)):
            return True

        # Stage 2.00: one low-overhead pressure summon in the slot freed by
        # removing Ether Nightmare.  It never outranks PI/core shutdown.
        if (yield from self._cast_evas(min_energy_pct=0.45)):
            return True

        # Stage 1.63: Epidemic is deliberately retired from active team
        # dispatch.  Keep the helper/loadout compatibility code so older bars do
        # not break matching, but no controller spends combat actions on it.

        # Web/Phantom are optional late preparations. Phantom is particularly
        # natural here because this Mesmer actually invests in Illusion Magic.
        if (yield from self._cast_web_setup()):
            return True
        if (yield from self._cast_phantom_pain()):
            return True

        # Legacy compatibility only: if Fragility is still left on Ineptitude,
        # prepare it after the controller's own control/Phantom opportunities.
        # The recommended Stage 1.57 test moves the single Fragility copy to one
        # eSurge so this Mesmer no longer starves Phantom with fresh feeder setup.
        if (yield from self._cast_fragility()):
            return True

        # Direct damage on an already hexed shared focus; never create a
        # separate packet or spend a fresh preparation hex on a dying foe.
        if (yield from self._cast_accumulated_offense()):
            return True

        target = self._pick_arcane_conundrum_target()
        if target and (yield from self._cast_targeted(Arcane_Conundrum_ID, target)):
            return True

        if snap.energy_pct <= 0.75 and (yield from self._cast_power_drain(0.75)):
            return True

        yield
