from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from Py4GWCoreLib import AgentArray, Profession, Range
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.Player import Player

DEFAULT_FILTER_RANGE = Range.Spellcast.value
DEFAULT_CLUSTER_RADIUS = Range.Adjacent.value
_PLAN_CACHE_MS: Final[int] = 45
# Compatibility/policy marker: retain_live_cleanup is evaluated before normal
# cluster-switch stability. A committed cleanup target must survive discovery
# of a denser neighbour until that target dies or becomes invalid.
_RETAIN_LIVE_CLEANUP_POLICY: Final[str] = "retain_live_cleanup"
_NORMAL_SWITCH_STABILITY_MS: Final[int] = 450
_GROUP_SWITCH_SIZE_ADVANTAGE: Final[int] = 2
_SHARED_PLAN_TTL_MS: Final[int] = 1200
_SHARED_PLAN_HEARTBEAT_MS: Final[int] = 350
_SHARED_PLAN_POST_SKEW_MS: Final[int] = 160

# Best-effort cross-account plan broadcast.  These are state keys, not skill IDs.
_TEAM_PLAN_GROUP_KEY = 948100
_TEAM_PLAN_AOE_KEY = 948101
_TEAM_PLAN_FOCUS_KEY = 948102
_TEAM_PLAN_MEMBER_KEY = 948103
_TEAM_PLAN_VERSION_KEY = 948104
_TEAM_PLAN_SECONDARY_KEY = 948105
_TEAM_PLAN_REASON_KEYS = {
    "special_priority": 948120,
    "locked_low_hp_finisher": 948121,
    "canonical_cluster": 948122,
    "sequential_cleanup": 948123,
    "retain_live_cleanup": 948124,
    "retain_live_execute_until_dead": 948129,
    "retain_live_primary_focus": 948131,
    "support_pair_cluster": 948132,
    "retain_live_support_pair": 948133,
    "engagement_lock": 948134,
    "engagement_wounded_cleanup": 948135,
    "engagement_disperse_cleanup": 948136,
    "engagement_boss_focus": 948137,
    "engagement_boss_execute": 948138,
    "engagement_boss_healer_first": 948139,
    "engagement_boss_healer_stall": 948140,
    "engagement_boss_healer_lock": 948141,
    "same_combat_group_new_aoe_center": 948125,
    "retain_current_group_transient_gap": 948126,
    "new_group_stable": 948127,
    "hold_old_group_candidate": 948128,
    "urgoz_room1_melee_collapse": 948130,
}
_TEAM_PLAN_REASON_BY_KEY = {v: k for k, v in _TEAM_PLAN_REASON_KEYS.items()}
_TEAM_PLAN_MODE_KEYS = {
    "cluster": 948110,
    "cleanup": 948111,
    "low_hp_finish": 948112,
    "st_defense": 948113,
    "krummrinde": 948114,
}
_TEAM_PLAN_MODE_BY_KEY = {v: k for k, v in _TEAM_PLAN_MODE_KEYS.items()}

# Stage 1.66: transaction-like shared plan publication using the existing
# generic whiteboard. PostLock has no multi-row atomic primitive, so each
# publication uses one of four generation slots and a COMMIT row written last.
# Readers never consume rows from an uncommitted/incomplete generation.
_TEAM_PLAN_TX_BASE = 948200
_TEAM_PLAN_TX_SLOTS = 4
_TEAM_PLAN_TX_STRIDE = 16
_TX_GEN = 0
_TX_SESSION = 1
_TX_VERSION = 2
_TX_GROUP = 3
_TX_AOE = 4
_TX_FOCUS = 5
_TX_MODE = 6
_TX_REASON = 7
_TX_MEMBER_COUNT = 8
_TX_MEMBER = 9
_TX_COMMIT = 10
_TEAM_PLAN_MODE_CODES = {"cluster": 1, "cleanup": 2, "low_hp_finish": 3, "st_defense": 4, "krummrinde": 5}
_TEAM_PLAN_MODE_BY_CODE = {v: k for k, v in _TEAM_PLAN_MODE_CODES.items()}
_TEAM_PLAN_REASON_CODES = {name: idx + 1 for idx, name in enumerate(_TEAM_PLAN_REASON_KEYS)}
_TEAM_PLAN_REASON_BY_CODE = {v: k for k, v in _TEAM_PLAN_REASON_CODES.items()}


@dataclass(frozen=True, slots=True)
class TeamCombatPlan:
    version: int = 0
    created_at_ms: int = 0
    mode: str = "none"
    group_anchor: int = 0
    aoe_anchor: int = 0
    focus_target: int = 0
    member_ids: tuple[int, ...] = ()
    reason: str = "none"
    source: str = "local"
    secondary_focus_target: int = 0


_cached_plan: TeamCombatPlan = TeamCombatPlan()
_cached_plan_tick: int = 0
# Per-plan geometry snapshot: one expensive native Agent.GetXY per actor in
# each 45-ms tactical recomputation, not once per O(N^2) distance comparison.
# Disabled between plan computations, so callers outside the plan read live
# positions and the cached plan's existing TTL/reaction speed is unchanged.
_plan_geometry_xy: dict[int, tuple[float, float]] | None = None
_plan_geometry_player_xy: tuple[float, float] | None = None
_plan_version: int = 0
_switch_candidate_signature: tuple[int, ...] = ()
_switch_candidate_since_ms: int = 0
_last_shared_post_ms: int = 0
_last_shared_post_version: int = 0
_shared_publication_generation: int = 0
_last_complete_shared_plan: TeamCombatPlan = TeamCombatPlan()
_last_complete_shared_expires_ms: int = 0
_last_complete_shared_session: int = 0

# Engaged-packet memory.  A connected-group snapshot must NOT be forgotten
# just because its members scatter or a nearby packet grows larger.
_ENGAGEMENT_RELEASE_OUT_OF_RANGE_MS: Final[int] = 650
_ENGAGEMENT_WOUNDED_THRESHOLD: Final[float] = 0.35
_engagement_members: tuple[int, ...] = ()
_engagement_out_of_range_since_ms: int = 0
_engagement_last_instance_uptime_ms: int = -1
_engagement_last_map_id: int = 0

# Adaptive boss/healer focus is deliberately scoped to the ENGAGED cohort.
# No chase outside spell range and no change to normal encounter selection.
_BOSS_HP_SAMPLE_MS: Final[int] = 750
_BOSS_HP_WINDOW_MS: Final[int] = 8000
_BOSS_STALL_MIN_MS: Final[int] = 6000
_BOSS_HEAL_SUM_THRESHOLD: Final[float] = 0.07
_BOSS_FINISH_HP: Final[float] = 0.15
_boss_track_id: int = 0
_boss_samples: list[tuple[int, float]] = []
_boss_first_focus_ms: int = 0
_boss_healer_target: int = 0
_boss_healer_phase: bool = False
_boss_last_decision: tuple[int, str] = (0, "")
# Bound expensive knowledge/native reads to ~1 Hz during large encounters.
_boss_glow_cache: dict[int, tuple[int, bool]] = {}
_boss_role_cache: dict[int, tuple[int, int]] = {}



# Stage 2.26: Urgoz room-1 anti-wipe focus. The opening Schaufler packet becomes
# dangerous when multiple melee attackers collapse on one or two party members
# while enemy Ritualists apply Splinter Weapon. Keep this strictly map/room local
# so normal cluster/execute policy elsewhere is unchanged.
_URGOZ_MAP_ID: Final[int] = 266
_URGOZ_ROOM1_X_MIN: Final[float] = 10500.0
_URGOZ_ROOM1_X_MAX: Final[float] = 14000.0
_URGOZ_ROOM1_Y_MIN: Final[float] = 5200.0
_URGOZ_ROOM1_Y_MAX: Final[float] = 10200.0
_URGOZ_ROOM1_MELEE_ENGAGE_RADIUS: Final[float] = float(Range.Nearby.value)
_URGOZ_ROOM1_MIN_MELEE_STACK: Final[int] = 2
_URGOZ_ROOM1_MELEE_LOG_MS: Final[int] = 1200
_last_urgoz_room1_melee_log_ms: int = 0


def _now_ms() -> int:
    try:
        import PySystem
        return int(PySystem.get_tick_count64() or 0)
    except Exception:
        return 0


def _geometry_xy(agent_id: int) -> tuple[float, float]:
    aid = int(agent_id)
    cache = _plan_geometry_xy
    if cache is not None and aid in cache:
        return cache[aid]
    xy = Agent.GetXY(aid)
    if cache is not None:
        cache[aid] = xy
    return xy


def _distance(agent_id: int) -> float:
    try:
        from Py4GWCoreLib.Py4GWcorelib import Utils
        player_xy = _plan_geometry_player_xy
        if player_xy is None:
            player_xy = Player.GetXY()
        return float(Utils.Distance(player_xy, _geometry_xy(int(agent_id))))
    except Exception:
        return 999999.0


def _distance_between(aid: int, bid: int) -> float:
    try:
        from Py4GWCoreLib.Py4GWcorelib import Utils
        return float(Utils.Distance(_geometry_xy(int(aid)), _geometry_xy(int(bid))))
    except Exception:
        return 999999.0


def _valid_alive(agent_id: int) -> bool:
    try:
        aid = int(agent_id or 0)
        return aid > 0 and Agent.IsValid(aid) and Agent.IsAlive(aid)
    except Exception:
        return False


def _valid(agent_id: int, filter_range: float = DEFAULT_FILTER_RANGE) -> bool:
    return bool(_valid_alive(agent_id) and _distance(int(agent_id)) <= float(filter_range))


def enemy_professions(agent_id: int) -> tuple[int, int]:
    try:
        p, s = Agent.GetProfessions(int(agent_id))
        return int(getattr(p, "value", p) or 0), int(getattr(s, "value", s) or 0)
    except Exception:
        return (0, 0)


def is_support_or_caster(agent_id: int) -> bool:
    p, s = enemy_professions(agent_id)
    ids = {
        int(getattr(Profession.Monk, "value", Profession.Monk)),
        int(getattr(Profession.Ritualist, "value", Profession.Ritualist)),
        int(getattr(Profession.Mesmer, "value", Profession.Mesmer)),
        int(getattr(Profession.Elementalist, "value", Profession.Elementalist)),
        int(getattr(Profession.Necromancer, "value", Profession.Necromancer)),
    }
    return p in ids or s in ids


def count_adjacent_enemies(agent_id: int, radius: float = DEFAULT_CLUSTER_RADIUS) -> int:
    if not _valid(agent_id):
        return 0
    try:
        arr = AgentArray.GetEnemyArray()
        arr = AgentArray.Filter.ByDistance(arr, Agent.GetXY(int(agent_id)), float(radius))
        arr = AgentArray.Filter.ByCondition(arr, lambda eid: Agent.IsValid(eid) and Agent.IsAlive(eid))
        return len(arr or [])
    except Exception:
        return 0


def _reachable_enemies(filter_range: float) -> tuple[int, ...]:
    try:
        return tuple(sorted({int(eid) for eid in (AgentArray.GetEnemyArray() or []) if _valid(int(eid), filter_range)}))
    except Exception:
        return ()


def _connected_group(seed_id: int, *, filter_range: float, radius: float = DEFAULT_CLUSTER_RADIUS) -> tuple[int, ...]:
    """Return the connected enemy component containing seed_id.

    This is deliberately broader than one AoE center: changing the densest member
    inside one connected fight must not create a new team combat group.
    """
    seed = int(seed_id or 0)
    enemies = _reachable_enemies(float(filter_range))
    if seed <= 0 or seed not in enemies:
        return ()
    pending = [seed]
    seen = {seed}
    while pending:
        cur = pending.pop()
        for other in enemies:
            if other in seen:
                continue
            if _distance_between(cur, other) <= float(radius):
                seen.add(other)
                pending.append(other)
    return tuple(sorted(seen))


def _best_aoe_center(member_ids: tuple[int, ...], *, radius: float = DEFAULT_CLUSTER_RADIUS) -> int:
    members = tuple(int(v) for v in member_ids if _valid_alive(int(v)))
    if not members:
        return 0
    def key(aid: int) -> tuple[int, int, int]:
        coverage = sum(1 for mid in members if _distance_between(aid, mid) <= float(radius))
        # Stage 2.59: NEVER sacrifice AoE coverage merely to chase a remote
        # healer. Among equally dense centers, prefer a known healer/support
        # already in this same engaged packet. Control handles outliers.
        healer_priority = 0
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            roles = EnemyKnowledge.role_scores(int(aid))
            healer_priority = int(roles.get("healer", 0) or 0) + int(roles.get("support", 0) or 0) // 2
        except Exception:
            pass
        return int(coverage), int(healer_priority), -int(aid)
    return int(max(members, key=key))


def _support_focus_rank(agent_id: int, packet: tuple[int, ...]) -> tuple[int, int, int, int, int, float, int]:
    """Deterministic support-first kill priority inside one shared packet.

    Stage 2.46 deliberately chooses the *cluster first* and only then ranks two
    damage anchors inside that packet.  Monks and Ritualists therefore do not
    pull the team out of a superior AoE packet, but a healer/prot/support that is
    already in the selected packet is burned before generic damage dealers.
    """
    aid = int(agent_id or 0)
    p, s = enemy_professions(aid)
    try:
        monk = int(getattr(Profession.Monk, "value", Profession.Monk))
        rit = int(getattr(Profession.Ritualist, "value", Profession.Ritualist))
    except Exception:
        monk = rit = 0
    profession_tier = 0
    if monk and p == monk:
        profession_tier = 5
    elif rit and p == rit:
        profession_tier = 4
    elif monk and s == monk:
        profession_tier = 3
    elif rit and s == rit:
        profession_tier = 2

    healer = support = threat = 0
    try:
        from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
        scores = EnemyKnowledge.role_scores(aid)
        healer = int(scores.get("healer", 0) or 0)
        support = int(scores.get("support", 0) or 0)
        threat = int(EnemyKnowledge.threat_bonus(aid) or 0)
    except Exception:
        pass
    support_score = max(int(healer), int(support))
    # Names of known high-risk DoA HM foes affect only the existing packet's
    # focus score; they never trigger target acquisition outside that packet.
    try:
        from Py4GWCoreLib.Builds.Skills import GlobalCombatRules
        support_score += int(GlobalCombatRules.doa_target_bonus(aid))
    except Exception:
        pass
    support_class = 1 if profession_tier > 0 or support_score >= 70 else 0
    density = 0
    try:
        density = sum(1 for mid in packet if _distance_between(aid, int(mid)) <= float(DEFAULT_CLUSTER_RADIUS))
    except Exception:
        density = 1
    hp = float(_health(aid))
    # Prefer a known healer/prot/support first. Live role evidence outranks the
    # broad profession heuristic so a confirmed Restoration support Ritualist
    # beats a generic damage Monk; profession is the fallback when role evidence
    # is sparse. Density keeps both anchors useful AoE centers. Lower HP is only
    # a tiebreaker; the <=10% hard-cleanup mode is handled separately.
    return (int(support_class), int(support_score), int(profession_tier), int(density), int(threat), -float(hp), -aid)


def _damage_focus_pair(member_ids: tuple[int, ...], aoe_anchor: int = 0) -> tuple[int, int]:
    """Return at most two support-first anchors from one Adjacent packet.

    The connected combat component can be wider than one actual AoE packet.  To
    avoid reopening the old multi-cluster scatter, both anchors must lie inside
    the same Adjacent packet around the publisher-owned AoE center.
    """
    live = tuple(int(v) for v in member_ids if _valid_alive(int(v)))
    if not live:
        return 0, 0
    center = int(aoe_anchor or _best_aoe_center(live) or live[0])
    packet = tuple(
        int(v) for v in live
        if int(v) == center or _distance_between(center, int(v)) <= float(DEFAULT_CLUSTER_RADIUS)
    ) or live
    ranked = sorted(packet, key=lambda aid: _support_focus_rank(int(aid), packet), reverse=True)
    primary = int(ranked[0]) if ranked else 0
    secondary = int(ranked[1]) if len(ranked) > 1 else 0
    return int(primary), int(secondary)


def _normalize_cluster_focus_pair(plan: TeamCombatPlan, old: TeamCombatPlan, *, filter_range: float) -> TeamCombatPlan:
    if str(plan.mode) != "cluster":
        return TeamCombatPlan(
            plan.version, plan.created_at_ms, plan.mode, plan.group_anchor, plan.aoe_anchor,
            plan.focus_target, plan.member_ids, plan.reason, plan.source, 0,
        )
    members = tuple(int(v) for v in plan.member_ids if _valid(int(v), filter_range))
    if not members:
        return plan
    primary = int(plan.focus_target or 0)
    if primary not in members:
        primary = 0
    old_same_fight = bool(old.mode == "cluster" and _same_fight(tuple(old.member_ids), members))
    if old_same_fight and int(old.focus_target or 0) in members:
        primary = int(old.focus_target)
    base_primary, base_secondary = _damage_focus_pair(members, int(plan.aoe_anchor or 0))
    if primary <= 0:
        primary = int(base_primary)

    secondary = 0
    center = int(plan.aoe_anchor or primary)
    primary_in_packet = bool(
        int(primary) == int(center)
        or _distance_between(int(center), int(primary)) <= float(DEFAULT_CLUSTER_RADIUS)
    )
    old_secondary = int(getattr(old, "secondary_focus_target", 0) or 0)
    # Stage 2.49: a retained secondary is valid only inside the publisher's
    # *current* Adjacent packet, not merely in its transitive combat component.
    # The previous `(same_packet or ranked)` fallback could open a second AoE
    # front outside the chosen packet. Never replace it with a third local lane.
    if primary_in_packet and old_same_fight and old_secondary in members and old_secondary != primary:
        if _distance_between(center, old_secondary) <= float(DEFAULT_CLUSTER_RADIUS):
            secondary = int(old_secondary)
    if secondary <= 0 and primary_in_packet:
        same_packet = [
            int(aid) for aid in members
            if int(aid) != int(primary)
            and _distance_between(center, int(aid)) <= float(DEFAULT_CLUSTER_RADIUS)
        ]
        if same_packet:
            secondary = int(max(same_packet, key=lambda aid: _support_focus_rank(int(aid), members)))
    if secondary == primary:
        secondary = 0
    reason = str(plan.reason)
    if reason == "canonical_cluster":
        reason = "support_pair_cluster"
    elif old_same_fight and int(primary) == int(old.focus_target or 0):
        reason = "retain_live_support_pair"
    return TeamCombatPlan(
        plan.version, plan.created_at_ms, plan.mode, plan.group_anchor, plan.aoe_anchor,
        int(primary), members, str(reason), plan.source, int(secondary),
    )


def _plan_target(plan: TeamCombatPlan) -> int:
    if str(plan.mode) in {"cleanup", "low_hp_finish", "st_defense", "krummrinde"}:
        return int(plan.focus_target or plan.group_anchor or plan.aoe_anchor)
    return int(plan.aoe_anchor or plan.group_anchor or plan.focus_target)


def _health(agent_id: int) -> float:
    try:
        return max(0.0, float(Agent.GetHealth(int(agent_id)) or 0.0))
    except Exception:
        return -1.0


def _log_switch(old: TeamCombatPlan, new: TeamCombatPlan, *, filter_range: float) -> None:
    if int(old.version) == int(new.version):
        return
    old_target = int(_plan_target(old) or 0)
    new_target = int(_plan_target(new) or 0)
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "TEAM_COMBAT_PLAN_SWITCH",
            old_version=int(old.version), new_version=int(new.version),
            old_target_id=old_target,
            old_target_hp=round(float(_health(old_target)), 4) if old_target > 0 else -1.0,
            old_target_alive=bool(_valid_alive(old_target)) if old_target > 0 else False,
            old_target_distance=round(float(_distance(old_target)), 1) if old_target > 0 else -1.0,
            new_target_id=new_target,
            old_group=",".join(str(v) for v in old.member_ids),
            new_group=",".join(str(v) for v in new.member_ids),
            old_mode=str(old.mode), new_mode=str(new.mode),
            switch_reason=str(new.reason),
            filter_range=round(float(filter_range), 1),
            policy="stage1_57_stable_combat_group_cleanup_lock",
        )
    except Exception:
        pass




def _party_agent_ids() -> tuple[int, ...]:
    ids: set[int] = set()
    try:
        from Py4GWCoreLib.Party import Party
        for player in Party.GetPlayers() or []:
            login = int(getattr(player, "login_number", 0) or 0)
            aid = int(Party.Players.GetAgentIDByLoginNumber(login) or 0)
            if aid > 0 and _valid_alive(aid):
                ids.add(aid)
        for getter in ("GetHeroes", "GetHenchmen"):
            for member in getattr(Party, getter)() or []:
                aid = int(getattr(member, "agent_id", 0) or 0)
                if aid > 0 and _valid_alive(aid):
                    ids.add(aid)
    except Exception:
        pass
    return tuple(sorted(ids))


def _in_urgoz_first_room() -> bool:
    try:
        from Py4GWCoreLib.Map import Map
        if int(Map.GetMapID() or 0) != int(_URGOZ_MAP_ID):
            return False
        x, y = Player.GetXY()
        return bool(
            float(_URGOZ_ROOM1_X_MIN) <= float(x) <= float(_URGOZ_ROOM1_X_MAX)
            and float(_URGOZ_ROOM1_Y_MIN) <= float(y) <= float(_URGOZ_ROOM1_Y_MAX)
        )
    except Exception:
        return False


def _is_melee_enemy(agent_id: int) -> bool:
    aid = int(agent_id or 0)
    if not _valid_alive(aid):
        return False
    # Live weapon telemetry is the strongest signal when available. Avoid bows,
    # spears and caster weapons; those should stay under normal cluster policy.
    try:
        _wid, weapon_name = Agent.GetWeaponType(aid)
        name = str(weapon_name or "").strip().casefold().replace(" ", "_")
        if any(token in name for token in ("sword", "axe", "hammer", "dagger", "scythe")):
            return True
        if any(token in name for token in ("bow", "spear", "staff", "wand", "scepter")):
            return False
    except Exception:
        pass
    p, _s = enemy_professions(aid)
    melee_primary = {
        int(getattr(Profession.Warrior, "value", Profession.Warrior)),
        int(getattr(Profession.Assassin, "value", Profession.Assassin)),
        int(getattr(Profession.Dervish, "value", Profession.Dervish)),
    }
    return int(p) in melee_primary


def _urgoz_room1_melee_collapse_candidate(*, filter_range: float) -> TeamCombatPlan | None:
    """Return the dangerous opening melee packet, never a generic room override.

    Trigger only when at least two live melee enemies are already within nearby
    range of the same party member. If the pack splits across two victims, the
    denser collapse is killed first; after it thins out the normal canonical
    cluster/cleanup policy resumes automatically.
    """
    global _last_urgoz_room1_melee_log_ms
    if not _in_urgoz_first_room():
        return None
    party_ids = _party_agent_ids()
    if not party_ids:
        return None
    melee = tuple(
        eid for eid in _reachable_enemies(float(filter_range))
        if _is_melee_enemy(int(eid))
    )
    if len(melee) < int(_URGOZ_ROOM1_MIN_MELEE_STACK):
        return None

    victim_rows: list[tuple[int, int, tuple[int, ...]]] = []
    for ally_id in party_ids:
        attackers = tuple(sorted(
            int(eid) for eid in melee
            if _distance_between(int(eid), int(ally_id)) <= float(_URGOZ_ROOM1_MELEE_ENGAGE_RADIUS)
        ))
        if attackers:
            victim_rows.append((len(attackers), int(ally_id), attackers))
    if not victim_rows:
        return None
    victim_rows.sort(key=lambda row: (-int(row[0]), int(row[1])))
    stack_size, victim_id, attackers = victim_rows[0]
    if int(stack_size) < int(_URGOZ_ROOM1_MIN_MELEE_STACK):
        return None

    # Keep the target set melee-only. AoE consumers still hit any ranged enemy
    # standing on top of them, but the authoritative center cannot be pulled away
    # by a larger backline packet while the dangerous collapse exists.
    center = int(_best_aoe_center(tuple(attackers)) or 0)
    if center <= 0:
        return None
    now = int(_now_ms() or 0)
    if now > 0 and now - int(_last_urgoz_room1_melee_log_ms or 0) >= int(_URGOZ_ROOM1_MELEE_LOG_MS):
        _last_urgoz_room1_melee_log_ms = int(now)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "URGOZ_ROOM1_MELEE_COLLAPSE_FOCUS",
                victim_id=int(victim_id), melee_count=int(len(attackers)),
                melee_members=",".join(str(int(v)) for v in attackers),
                aoe_anchor=int(center),
                policy="stage2_26_room1_melee_stack_before_ranged_backline",
            )
        except Exception:
            pass
    return TeamCombatPlan(
        0, now, "cluster", min(int(v) for v in attackers), int(center), int(center),
        tuple(int(v) for v in attackers), "urgoz_room1_melee_collapse", "local",
    )


def _is_boss(agent_id: int) -> bool:
    """Use the existing native boss-glow API; never infer boss from level."""
    aid = int(agent_id)
    now = int(_now_ms())
    saved = _boss_glow_cache.get(aid)
    if saved and now > 0 and now - int(saved[0]) < 1200:
        return bool(saved[1])
    try:
        value = bool(Agent.HasBossGlow(aid))
    except Exception:
        value = False
    if now > 0:
        _boss_glow_cache[aid] = (now, value)
    return value


def _boss_healer_strength(agent_id: int) -> int:
    """EnemyKnowledge supplies profession and observed healing evidence."""
    aid = int(agent_id)
    now = int(_now_ms())
    saved = _boss_role_cache.get(aid)
    if saved and now > 0 and now - int(saved[0]) < 700:
        return int(saved[1])
    try:
        from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
        strength = int(EnemyKnowledge.role_scores(aid).get("healer", 0) or 0)
    except Exception:
        strength = 0
    if now > 0:
        _boss_role_cache[aid] = (now, strength)
    return strength


def _boss_engagement_candidate(filter_range: float, now: int) -> TeamCombatPlan | None:
    """Acquire only a boss already within normal cast range, never seek/chase."""
    enemies = _reachable_enemies(float(filter_range))
    bosses = [int(a) for a in enemies if _is_boss(int(a))]
    if not bosses:
        return None
    boss = int(min(bosses, key=lambda a: (_distance(int(a)), int(a))))
    packet = set(_connected_group(boss, filter_range=float(filter_range)) or (boss,))
    # A healer can stand just outside the tightly connected AoE packet. Include
    # it only when close to this boss, truly in spell range, and confidently
    # healer-classified, preventing a jump to an unrelated distant cluster.
    for enemy in enemies:
        eid = int(enemy)
        if eid in packet or _distance_between(boss, eid) > float(Range.Nearby.value):
            continue
        if _boss_healer_strength(eid) >= 70:
            packet.add(eid)
    members = tuple(sorted(packet))
    aoe = int(_best_aoe_center(members) or boss)
    return TeamCombatPlan(0, int(now), "cluster", min(members), aoe, boss, members,
                          "engagement_boss_focus", "local", 0)


def _boss_reset() -> None:
    global _boss_track_id, _boss_samples, _boss_first_focus_ms
    global _boss_healer_target, _boss_healer_phase, _boss_last_decision
    _boss_track_id = 0
    _boss_samples = []
    _boss_first_focus_ms = 0
    _boss_healer_target = 0
    _boss_healer_phase = False
    _boss_last_decision = (0, "")
    _boss_glow_cache.clear()
    _boss_role_cache.clear()


def _boss_observe_hp(boss: int, now: int) -> tuple[bool, float, int]:
    """Short bounded history. Returns confirmed recovery/stall, healing sum, age."""
    global _boss_track_id, _boss_samples, _boss_first_focus_ms
    hp = float(_health(int(boss)))
    if hp < 0.0 or now <= 0:
        return False, 0.0, 0
    if boss != _boss_track_id:
        _boss_reset()
        _boss_track_id = int(boss)
        _boss_first_focus_ms = int(now)
    if not _boss_samples or now - _boss_samples[-1][0] >= int(_BOSS_HP_SAMPLE_MS):
        _boss_samples.append((int(now), hp))
    _boss_samples = [(t, value) for t, value in _boss_samples if int(now) - t <= int(_BOSS_HP_WINDOW_MS)]
    if not _boss_samples:
        return False, 0.0, 0
    # Sum positive health recovery rather than treating an idle full-health
    # boss as evidence of healing. Only enable time-based stalls after actually
    # damaging the boss, and avoid switching after just one minor HP fluctuation.
    recovered = sum(max(0.0, b - a) for (_, a), (_, b) in zip(_boss_samples, _boss_samples[1:]))
    elapsed = int(now) - int(_boss_samples[0][0])
    damaged = min(v for _t, v in _boss_samples) < 0.94
    heal_detected = bool(damaged and recovered >= float(_BOSS_HEAL_SUM_THRESHOLD)
                         and elapsed >= 2000)
    stalled = bool(damaged and elapsed >= int(_BOSS_STALL_MIN_MS)
                   and int(now) - int(_boss_first_focus_ms) >= int(_BOSS_STALL_MIN_MS)
                   and hp >= float(_boss_samples[0][1]) - 0.04)
    return bool(heal_detected or stalled), float(recovered), int(elapsed)


def _adaptive_boss_plan(plan: TeamCombatPlan, now: int, filter_range: float) -> TeamCombatPlan:
    """Central boss/healer policy; shared by the 5 Mesmers through the plan.

    Preserve emergency ST priorities, existing 5%% execution and committed
    engagement members. Never call movement/attack functions or alter skill
    timing. A healer is eligible only within spell range and the old fight.
    """
    global _boss_healer_target, _boss_healer_phase
    global _boss_last_decision
    if plan.mode not in {"cluster", "cleanup", "low_hp_finish"}:
        return plan
    members = tuple(sorted({int(a) for a in plan.member_ids if _valid(int(a), filter_range)}))
    bosses = [int(a) for a in members if _is_boss(int(a))]
    if not bosses:
        return plan
    boss = int(min(bosses, key=lambda a: (_health(int(a)), int(a))))
    hp = float(_health(boss))
    if hp < 0:
        return plan
    compromised, recovered, observed_ms = _boss_observe_hp(boss, int(now))
    healers = [int(a) for a in members if int(a) != boss and _boss_healer_strength(int(a)) >= 70]
    # Nearly-dead foes are not abandoned; the standard <=5%% 2+1 execute
    # claims still run before normal signet selection.
    wounded_finish = [int(a) for a in members if 0 < float(_health(a)) <= 0.05]
    if wounded_finish:
        focus = int(min(wounded_finish, key=lambda a: (float(_health(a)), a)))
        reason = "engagement_boss_execute"
    elif 0 < hp <= float(_BOSS_FINISH_HP):
        focus = boss
        reason = "engagement_boss_execute"
    else:
        # If two confirmed healers stand in the boss encounter, burn them first.
        # Otherwise start on boss; switch to healer only on measured stalled HP
        # or repeated healing. Once committed, finish the reachable healer.
        if len(healers) >= 2:
            _boss_healer_phase = True
        elif compromised and healers:
            _boss_healer_phase = True
        if _boss_healer_target not in healers:
            _boss_healer_target = 0
        if _boss_healer_phase and healers:
            if _boss_healer_target <= 0:
                _boss_healer_target = int(min(healers, key=lambda a: (float(_health(a)), -_boss_healer_strength(a), a)))
            focus = int(_boss_healer_target)
            reason = ("engagement_boss_healer_first" if len(healers) >= 2
                      else "engagement_boss_healer_stall" if compromised
                      else "engagement_boss_healer_lock")
        else:
            focus = int(boss)
            reason = "engagement_boss_focus"
            _boss_healer_phase = False
            _boss_healer_target = 0
    # Do not recast a previous target after it has died, left range or is no
    # longer a member of this same encounter.
    if focus not in members:
        return plan
    chosen = TeamCombatPlan(0, int(now), "cleanup", min(members),
                            int(plan.aoe_anchor or boss), int(focus), members,
                            str(reason), "local", 0)
    if (int(focus), str(reason)) != _boss_last_decision:
        _boss_last_decision = (int(focus), str(reason))
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_state_event(
                "TEAM_ADAPTIVE_BOSS_FOCUS",
                signature_fields=("boss_id", "focus_id", "reason"),
                min_interval_ms=1200, heartbeat_ms=7000,
                boss_id=int(boss), boss_hp=round(hp, 4),
                focus_id=int(focus), focus_hp=round(float(_health(focus)), 4),
                reason=str(reason), healers=','.join(map(str, healers)),
                observed_ms=int(observed_ms), recovered_hp=round(recovered, 4),
                policy="engagement_scoped_no_chase_healer_commit",
            )
        except Exception:
            pass
    return chosen


def _raw_candidate(*, filter_range: float, minimum_enemies: int, consumer_role: str) -> TeamCombatPlan:
    now = _now_ms()
    try:
        from Py4GWCoreLib.Builds.Skills.CombatSense import (
            pick_special_priority_target,
            pick_locked_low_hp_finisher,
            pick_pressure_anchor,
            pick_single_target_anchor,
        )
        target = int(pick_special_priority_target(range_value=float(filter_range)) or 0)
        if target > 0 and _valid(target, filter_range):
            try:
                from Py4GWCoreLib.Builds.Skills import STDefensePriority
                from Py4GWCoreLib.Builds.Skills import SupportGuardSafety
                mode = ("st_defense" if (
                    STDefensePriority.is_priority_target(target)
                    or SupportGuardSafety.is_recent_panic_priority(target)
                ) else "krummrinde")
            except Exception:
                mode = "krummrinde"
            group = _connected_group(target, filter_range=float(filter_range)) or (target,)
            return TeamCombatPlan(0, now, mode, target, _best_aoe_center(group), target, group, "special_priority", "local")

        target = int(pick_locked_low_hp_finisher(range_value=float(filter_range), hp_threshold=0.10) or 0)
        if target > 0 and _valid(target, filter_range):
            group = _connected_group(target, filter_range=float(filter_range)) or (target,)
            return TeamCombatPlan(0, now, "low_hp_finish", target, _best_aoe_center(group), target, group, "locked_low_hp_finisher", "local")

        melee_plan = _urgoz_room1_melee_collapse_candidate(filter_range=float(filter_range))
        if melee_plan is not None:
            return melee_plan

        # Once engaged, only the original cohort is eligible. Re-scanning all
        # reachable bosses during every 45ms planning tick would be expensive
        # and could destabilize an otherwise committed encounter.
        if not _engagement_members:
            boss_plan = _boss_engagement_candidate(float(filter_range), int(now))
            if boss_plan is not None:
                return boss_plan

        anchor = int(pick_pressure_anchor(range_value=float(filter_range), minimum_enemies=int(minimum_enemies), player_target=0) or 0)
        if anchor > 0 and _valid(anchor, filter_range):
            group = _connected_group(anchor, filter_range=float(filter_range)) or (anchor,)
            aoe = int(_best_aoe_center(group) or anchor)
            primary, secondary = _damage_focus_pair(group, aoe)
            # Only an effect-confirmed Torment call *inside the selected combat
            # packet* may override the global kill focus. Do not chase another
            # packet; if effects are invisible this remains a no-op.
            try:
                from Py4GWCoreLib.Builds.Skills import GlobalCombatRules
                executing = [int(a) for a in group if GlobalCombatRules.torment_execute_active(int(a))]
                if executing:
                    primary = min(executing, key=lambda a: (float(_health(a)), a))
                    secondary = 0
            except Exception:
                pass
            # Three eSurges normally share one primary. Only large active
            # packets justify an optional 2+1 alternate damage anchor.
            if len(group) < 4:
                secondary = 0
            # Stage 2.44 compatibility marker (superseded by the pair below):
            # "cluster", min(group), _best_aoe_center(group), int(anchor)
            # Stage 2.46: select the cluster first, then expose two support-first
            # damage anchors inside that one packet.  This preserves AoE value
            # while concentrating kills on healer/prot/support targets.
            return TeamCombatPlan(
                0, now, "cluster", min(group), aoe, int(primary or anchor), group,
                "support_pair_cluster", "local", int(secondary),
            )

        target = int(pick_single_target_anchor(
            range_value=float(filter_range), player_target=0, assignment_slot=None,
            consumer_role=str(consumer_role or "party"),
        ) or 0)
        if target > 0 and _valid(target, filter_range):
            return TeamCombatPlan(0, now, "cleanup", target, target, target, (target,), "sequential_cleanup", "local")
    except Exception:
        pass
    return TeamCombatPlan(0, now, "none", 0, 0, 0, (), "no_candidate", "local")


def _engagement_health_tier(agent_id: int) -> int:
    """Prefer imminent kills, then wounded, then live support in the SAME fight."""
    hp = float(_health(int(agent_id)))
    if hp < 0:
        return 0
    if hp <= 0.10:
        return 5
    if hp <= _ENGAGEMENT_WOUNDED_THRESHOLD:
        return 4
    p, secondary = enemy_professions(int(agent_id))
    try:
        monk = int(getattr(Profession.Monk, "value", Profession.Monk))
        ritualist = int(getattr(Profession.Ritualist, "value", Profession.Ritualist))
        ranger = int(getattr(Profession.Ranger, "value", Profession.Ranger))
        priority_role = p in (monk, ritualist, ranger) or secondary in (monk, ritualist)
    except Exception:
        priority_role = False
    if hp <= 0.60:
        return 3 if priority_role else 2
    return 1 if priority_role else 0


def _engagement_target(eligible: tuple[int, ...], old: TeamCombatPlan) -> int:
    """Deterministic low-health cleanup inside the original, reachable cohort."""
    if not eligible:
        return 0
    old_target = int(old.focus_target or 0)
    def rank(aid: int) -> tuple[int, int, int, float, int]:
        tier = _engagement_health_tier(int(aid))
        try:
            p, sec = enemy_professions(int(aid))
            ranger = int(getattr(Profession.Ranger, "value", Profession.Ranger))
            ranger_rank = int(p == ranger or sec == ranger)
        except Exception:
            ranger_rank = 0
        # Finishers first; among comparably wounded foes protect the existing
        # focus against pointless jitter. Confirmed healing/support comes first
        # outside the hard-finisher tiers, Rangers next as requested.
        healer_rank = int(is_support_or_caster(int(aid)))
        try:
            from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
            roles = EnemyKnowledge.role_scores(int(aid))
            healer_rank = max(healer_rank, 3 if int(roles.get("healer", 0) or 0) >= 30
                              else 2 if int(roles.get("support", 0) or 0) >= 30 else 0)
        except Exception:
            pass
        hp = float(_health(int(aid)))
        if tier >= 4:
            # Inside hard-finisher bands the most nearly dead victim wins.
            return (int(tier), -hp, int(healer_rank), int(ranger_rank), int(aid == old_target))
        return (int(tier), int(healer_rank), int(ranger_rank), -hp, int(aid == old_target))
    return int(max(eligible, key=rank))


def _engagement_plan(eligible: tuple[int, ...], old: TeamCombatPlan, now: int) -> TeamCombatPlan:
    """One old cohort even when Adjacent graph breaks into smaller islands."""
    live = tuple(sorted(set(int(aid) for aid in eligible)))
    if not live:
        return TeamCombatPlan()
    focus = _engagement_target(live, old)
    aoe = _best_aoe_center(live) or focus
    density = sum(1 for aid in live if _distance_between(int(aoe), int(aid)) <= float(DEFAULT_CLUSTER_RADIUS))
    wounded = 0.0 < _health(focus) <= float(_ENGAGEMENT_WOUNDED_THRESHOLD)
    # For dispersed mobs/low-HP targets, consume the old fight via the single
    # target cleanup API; the authoritative member list remains the WHOLE cohort
    # so the existing 2+1 execute allocator still has the other victims.
    if wounded or density < 2:
        reason = "engagement_wounded_cleanup" if wounded else "engagement_disperse_cleanup"
        return TeamCombatPlan(0, now, "cleanup", min(live), int(aoe), int(focus), live,
                              reason, "local", 0)
    return TeamCombatPlan(0, now, "cluster", min(live), int(aoe), int(focus), live,
                          "engagement_lock", "local", 0)


def _engagement_instance_reset_if_needed() -> None:
    """Never retain agent IDs across a new map instance."""
    global _engagement_members, _engagement_out_of_range_since_ms
    global _engagement_last_instance_uptime_ms, _engagement_last_map_id
    global _cached_plan, _cached_plan_tick
    try:
        from Py4GWCoreLib.Map import Map
        uptime = int(Map.GetInstanceUptime() or 0)
        map_id = int(Map.GetMapID() or 0)
    except Exception:
        return
    reset = bool(
        (_engagement_last_map_id > 0 and map_id > 0 and map_id != _engagement_last_map_id)
        or (_engagement_last_instance_uptime_ms >= 0
            and uptime + 2500 < _engagement_last_instance_uptime_ms)
    )
    if reset:
        _engagement_members = ()
        _engagement_out_of_range_since_ms = 0
        _boss_reset()
        _cached_plan = TeamCombatPlan()
        _cached_plan_tick = 0
    _engagement_last_map_id = map_id
    _engagement_last_instance_uptime_ms = uptime


def _engagement_log(plan: TeamCombatPlan, active: tuple[int, ...]) -> None:
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_state_event(
            "TEAM_ENGAGEMENT_LOCK",
            signature_fields=("mode", "focus_target", "members"),
            min_interval_ms=1200, heartbeat_ms=5000,
            mode=str(plan.mode), focus_target=int(plan.focus_target),
            focus_hp=round(float(_health(int(plan.focus_target))), 4),
            members=",".join(str(a) for a in active),
            active_count=len(active), reason=str(plan.reason),
            policy="original_cohort_wounded_cleanup_no_chase",
        )
    except Exception:
        pass


def _same_fight(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    return bool(set(int(x) for x in a) & set(int(x) for x in b))


def _stabilize_candidate(candidate: TeamCombatPlan, *, filter_range: float) -> TeamCombatPlan:
    global _plan_version, _switch_candidate_signature, _switch_candidate_since_ms
    global _engagement_members, _engagement_out_of_range_since_ms
    now = int(_now_ms())
    _engagement_instance_reset_if_needed()
    old = _cached_plan
    if _engagement_members:
        # Prune dead IDs but keep live members which temporarily run beyond
        # spell range; they remain in the encounter history, not cast targets.
        _engagement_members = tuple(a for a in _engagement_members if _valid_alive(a))
    engaged_reachable = tuple(a for a in _engagement_members if _valid(a, filter_range))
    if engaged_reachable:
        _engagement_out_of_range_since_ms = 0
    elif _engagement_members:
        if not _engagement_out_of_range_since_ms:
            _engagement_out_of_range_since_ms = now
        if now <= 0 or now - _engagement_out_of_range_since_ms >= _ENGAGEMENT_RELEASE_OUT_OF_RANGE_MS:
            _engagement_members = ()
            _engagement_out_of_range_since_ms = 0
    else:
        _engagement_out_of_range_since_ms = 0
        if _boss_track_id:
            _boss_reset()
    # Keep the historic cleanup commitment explicit and early in this routine:
    # retain_live_cleanup is stronger than ordinary cluster switch stability.
    retain_cleanup_reason = "retain_live_cleanup"

    old_live = tuple(v for v in old.member_ids if _valid(int(v), filter_range))
    old_focus_live = int(old.focus_target or 0) > 0 and _valid(int(old.focus_target), filter_range)
    candidate_room1_melee = bool(str(candidate.reason) == "urgoz_room1_melee_collapse")
    old_focus_hp = float(_health(int(old.focus_target or 0))) if old_focus_live else -1.0
    old_true_finisher = bool(old_focus_live and 0.0 <= old_focus_hp <= 0.10)
    # Preserve the Stage 2.26 room-1 rule explicitly: room-1 melee collapse may
    # seize an ordinary cluster, but never abandon a live true finisher.
    room1_can_preempt = candidate_room1_melee and not old_true_finisher
    old_cleanup_committed = bool(old_focus_live and old.mode in {"cleanup", "low_hp_finish"})
    old_cluster_committed = bool(old_focus_live and old.mode == "cluster" and int(old.focus_target or 0) > 0)

    # Stage 2.44 focus doctrine: once the team starts burning a primary foe,
    # keep that exact target until it is dead/invalid. This is intentionally
    # stronger than the old per-skill overkill avoidance that spread four
    # Mesmers and the RoJ heroes over several members of one connected fight.
    # Special mechanics may still seize the shared plan, and a clearly larger
    # *different* packet may win only through the existing stability gate.
    if engaged_reachable and candidate.mode not in {"st_defense", "krummrinde"} and not room1_can_preempt:
        chosen = _engagement_plan(engaged_reachable, old, now)
        reason = str(chosen.reason)
        _switch_candidate_signature = ()
        _switch_candidate_since_ms = 0
        _engagement_log(chosen, engaged_reachable)
    elif candidate.mode in {"st_defense", "krummrinde"}:
        # Emergency protection always interrupts ordinary kill commitment.
        chosen = candidate
        reason = candidate.reason
    elif old_cleanup_committed:
        focus = int(old.focus_target)
        group = _connected_group(focus, filter_range=float(filter_range)) or (focus,)
        chosen = TeamCombatPlan(
            0, now, "low_hp_finish", focus, _best_aoe_center(group), focus, group,
            "retain_live_execute_until_dead", "local",
        )
        reason = "retain_live_execute_until_dead"
    elif old_cluster_committed and old_true_finisher:
        focus = int(old.focus_target)
        group = _connected_group(focus, filter_range=float(filter_range)) or (focus,)
        chosen = TeamCombatPlan(
            0, now, "low_hp_finish", focus, _best_aoe_center(group), focus, group,
            "retain_live_execute_until_dead", "local",
        )
        reason = "retain_live_execute_until_dead"
    elif candidate.mode in {"st_defense", "krummrinde"}:
        chosen = candidate
        reason = candidate.reason
    elif room1_can_preempt and not (old_cluster_committed and int(old.focus_target or 0) in set(candidate.member_ids)):
        chosen = candidate
        reason = candidate.reason
    elif old_cluster_committed:
        focus = int(old.focus_target)
        if candidate.mode == "cluster" and _same_fight(old_live, candidate.member_ids):
            group = _connected_group(focus, filter_range=float(filter_range)) or tuple(sorted(set(old_live) | set(candidate.member_ids)))
            chosen = TeamCombatPlan(
                0, now, "cluster", min(group), _best_aoe_center(group), focus, group,
                "retain_live_primary_focus", "local",
            )
            reason = "retain_live_primary_focus"
            _switch_candidate_signature = ()
            _switch_candidate_since_ms = 0
        elif candidate.mode == "none":
            group = _connected_group(focus, filter_range=float(filter_range)) or old_live or (focus,)
            chosen = TeamCombatPlan(
                0, now, "cluster", min(group), _best_aoe_center(group), focus, group,
                "retain_live_primary_focus", "local",
            )
            reason = "retain_live_primary_focus"
        elif candidate.member_ids and not _same_fight(old_live, candidate.member_ids):
            sig = tuple(candidate.member_ids)
            if sig != _switch_candidate_signature:
                _switch_candidate_signature = sig
                _switch_candidate_since_ms = now
            stable_ms = max(0, now - int(_switch_candidate_since_ms or now))
            advantage = int(len(candidate.member_ids)) - int(len(old_live))
            if stable_ms >= int(_NORMAL_SWITCH_STABILITY_MS) and advantage >= int(_GROUP_SWITCH_SIZE_ADVANTAGE):
                chosen = candidate
                reason = f"new_group_stable_{stable_ms}ms_advantage_{advantage}"
                _switch_candidate_signature = ()
                _switch_candidate_since_ms = 0
            else:
                group = _connected_group(focus, filter_range=float(filter_range)) or old_live or (focus,)
                chosen = TeamCombatPlan(
                    0, now, "cluster", min(group), _best_aoe_center(group), focus, group,
                    f"hold_old_group_candidate_{stable_ms}ms_advantage_{advantage}", "local",
                )
                reason = chosen.reason
        else:
            group = _connected_group(focus, filter_range=float(filter_range)) or old_live or (focus,)
            chosen = TeamCombatPlan(
                0, now, "cluster", min(group), _best_aoe_center(group), focus, group,
                "retain_live_primary_focus", "local",
            )
            reason = "retain_live_primary_focus"
    elif room1_can_preempt:
        chosen = candidate
        reason = candidate.reason
    else:
        if old.mode in {"cleanup", "low_hp_finish"} and old_focus_live:
            group = _connected_group(int(old.focus_target), filter_range=float(filter_range)) or (int(old.focus_target),)
            chosen = TeamCombatPlan(0, now, old.mode, int(old.focus_target), _best_aoe_center(group), int(old.focus_target), group, retain_cleanup_reason, "local")
            reason = retain_cleanup_reason
        elif old.mode == "cluster" and old_live:
            if candidate.mode == "cluster" and _same_fight(old_live, candidate.member_ids):
                merged_seed = int(candidate.aoe_anchor or candidate.group_anchor or old.aoe_anchor or old.group_anchor)
                group = _connected_group(merged_seed, filter_range=float(filter_range)) or tuple(sorted(set(old_live) | set(candidate.member_ids)))
                focus = int(candidate.focus_target or 0)
                chosen = TeamCombatPlan(0, now, "cluster", min(group), _best_aoe_center(group), focus, group, "same_combat_group_new_aoe_center", "local")
                reason = "same_combat_group_new_aoe_center"
                _switch_candidate_signature = ()
                _switch_candidate_since_ms = 0
            elif candidate.mode == "none":
                chosen = TeamCombatPlan(0, now, "cluster", min(old_live), _best_aoe_center(old_live), 0, old_live, "retain_current_group_transient_gap", "local")
                reason = "retain_current_group_transient_gap"
            elif candidate.member_ids and not _same_fight(old_live, candidate.member_ids):
                sig = tuple(candidate.member_ids)
                if sig != _switch_candidate_signature:
                    _switch_candidate_signature = sig
                    _switch_candidate_since_ms = now
                stable_ms = max(0, now - int(_switch_candidate_since_ms or now))
                advantage = int(len(candidate.member_ids)) - int(len(old_live))
                if stable_ms >= int(_NORMAL_SWITCH_STABILITY_MS) and advantage >= int(_GROUP_SWITCH_SIZE_ADVANTAGE):
                    chosen = candidate
                    reason = f"new_group_stable_{stable_ms}ms_advantage_{advantage}"
                    _switch_candidate_signature = ()
                    _switch_candidate_since_ms = 0
                else:
                    chosen = TeamCombatPlan(0, now, "cluster", min(old_live), _best_aoe_center(old_live), 0, old_live, f"hold_old_group_candidate_{stable_ms}ms_advantage_{advantage}", "local")
                    reason = chosen.reason
            else:
                chosen = candidate
                reason = candidate.reason
        else:
            chosen = candidate
            reason = candidate.reason
            _switch_candidate_signature = ()
            _switch_candidate_since_ms = 0

    # Take the FIRST actual combat packet as the engagement cohort. Follow-up
    # plans may narrow it for wounded cleanup, but cannot replace it merely
    # because another packet now has more neighbours. Never learn emergency
    # ST-defense targets as normal offensive cohort membership.
    if not _engagement_members and chosen.mode in {"cluster", "cleanup", "low_hp_finish"}:
        _engagement_members = tuple(sorted({int(a) for a in chosen.member_ids if _valid(int(a), filter_range)}))
        _engagement_out_of_range_since_ms = 0

    # The adaptive boss policy takes precedence over generic engagement focus,
    # not over ST danger/room1 emergency, and uses only the chosen fight's IDs.
    chosen = _adaptive_boss_plan(chosen, now, float(filter_range))
    reason = str(chosen.reason)

    _normalize_pair = globals().get("_normalize_cluster_focus_pair")
    if callable(_normalize_pair):
        chosen = _normalize_pair(chosen, old, filter_range=float(filter_range))

    same_identity = False
    if old.mode == chosen.mode == "cluster" and _same_fight(old.member_ids, chosen.member_ids):
        same_identity = (
            int(old.focus_target or 0) == int(chosen.focus_target or 0)
            and int(getattr(old, "secondary_focus_target", 0) or 0)
            == int(getattr(chosen, "secondary_focus_target", 0) or 0)
        )
    elif old.mode == chosen.mode and old.mode in {"cleanup", "low_hp_finish", "st_defense", "krummrinde"}:
        same_identity = int(old.focus_target or 0) == int(chosen.focus_target or 0)
    elif old.mode == chosen.mode == "none":
        same_identity = True
    version = int(old.version)
    if not same_identity:
        _plan_version = max(int(_plan_version) + 1, int(old.version) + 1, 1)
        version = int(_plan_version)
    elif version <= 0 and chosen.mode != "none":
        _plan_version = max(1, int(_plan_version) + 1)
        version = int(_plan_version)
    try:
        return TeamCombatPlan(
            version, now, chosen.mode, chosen.group_anchor, chosen.aoe_anchor, chosen.focus_target,
            chosen.member_ids, str(reason), "local", int(getattr(chosen, "secondary_focus_target", 0) or 0),
        )
    except TypeError:
        # Compatibility with the historical isolated contract tests that load
        # _stabilize_candidate with the original 9-field plan stub. Production
        # TeamCombatPlan carries the Stage-2.46 secondary anchor.
        return TeamCombatPlan(
            version, now, chosen.mode, chosen.group_anchor, chosen.aoe_anchor, chosen.focus_target,
            chosen.member_ids, str(reason), "local",
        )

def _owner_context() -> tuple[str, int]:
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        email = str(Player.GetAccountEmail() or "").strip()
        if not email:
            return "", 0
        gid = int(GLOBAL_CACHE.ShMem.GetAccountGroupByEmail(email) or 0)
        if gid <= 0:
            gid = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
        return email, max(0, gid)
    except Exception:
        return "", 0


def _is_shared_publisher() -> bool:
    try:
        from Py4GWCoreLib import Party
        return int(Player.GetAgentID() or 0) > 0 and int(Player.GetAgentID() or 0) == int(Party.GetPartyLeaderID() or 0)
    except Exception:
        return False


def _reason_key(reason: str) -> int:
    value = str(reason or "")
    for prefix, key in _TEAM_PLAN_REASON_KEYS.items():
        if value == prefix or value.startswith(prefix + "_"):
            return int(key)
    return 0


def _tx_key(slot: int, field: int) -> int:
    return int(_TEAM_PLAN_TX_BASE + (int(slot) % int(_TEAM_PLAN_TX_SLOTS)) * int(_TEAM_PLAN_TX_STRIDE) + int(field))


def _tx_key_info(key: int) -> tuple[int, int] | None:
    offset = int(key) - int(_TEAM_PLAN_TX_BASE)
    if offset < 0 or offset >= int(_TEAM_PLAN_TX_SLOTS) * int(_TEAM_PLAN_TX_STRIDE):
        return None
    slot, field = divmod(offset, int(_TEAM_PLAN_TX_STRIDE))
    if int(field) > int(_TX_COMMIT):
        return None
    return int(slot), int(field)


def _shared_session_signature() -> int:
    """Cross-client short-lived instance signature, using only existing APIs."""
    try:
        from Py4GWCoreLib.Map import Map
        from Py4GWCoreLib.Party import Party
        now = int(_now_ms() or 0)
        map_id = int(Map.GetMapID() or 0)
        party_id = int(Party.GetPartyID() or 0)
        hard_mode = 1 if bool(Party.IsHardMode()) else 0
        uptime = max(0, int(Map.GetInstanceUptime() or 0))
        # PySystem tick is shared by clients on the same host; subtracting the
        # native instance uptime yields a stable re-entry discriminator. A 5 s
        # bucket absorbs tiny per-client sampling skew.
        start_bucket = max(0, now - uptime) // 5000 if now > 0 else 0
        h = 2166136261
        for value in (map_id, party_id, hard_mode, start_bucket):
            value = int(value) & 0xFFFFFFFF
            for shift in (0, 8, 16, 24):
                h ^= (value >> shift) & 0xFF
                h = (h * 16777619) & 0xFFFFFFFF
        return max(1, int(h & 0x7FFFFFFF))
    except Exception:
        return 0


def _publisher_owner_is_current_leader(owner: str, gid: int) -> bool:
    """Reject stale/foreign publishers when account metadata can prove it."""
    try:
        from Py4GWCoreLib import GLOBAL_CACHE, Party
        leader = int(Party.GetPartyLeaderID() or 0)
        if leader <= 0:
            return True
        found_owner = False
        for account in GLOBAL_CACHE.ShMem.GetAllAccounts().AccountData:
            if not bool(getattr(account, "IsSlotActive", False)) or not bool(getattr(account, "IsAccount", False)):
                continue
            if int(getattr(account, "IsolationGroupID", 0) or 0) != int(gid):
                continue
            if str(getattr(account, "AccountEmail", "") or "") != str(owner):
                continue
            found_owner = True
            return int(getattr(getattr(account, "AgentData", None), "AgentID", 0) or 0) == leader
        # Older test/mocked shared-memory surfaces may not expose AccountData;
        # group+session+commit validation still applies in that case.
        return not found_owner
    except Exception:
        return True


def _reset_slots(slot_indexes: list[int]) -> None:
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        intents = GLOBAL_CACHE.ShMem.GetAllAccounts().Intents
    except Exception:
        return
    for idx in slot_indexes:
        try:
            intents[int(idx)].reset()
        except Exception:
            pass


def _post_shared_plan(plan: TeamCombatPlan) -> None:
    global _last_shared_post_ms, _last_shared_post_version, _shared_publication_generation
    if plan.mode == "none" or not plan.member_ids or not _is_shared_publisher():
        return
    now = int(_now_ms())
    if int(plan.version) == int(_last_shared_post_version) and now - int(_last_shared_post_ms) < int(_SHARED_PLAN_HEARTBEAT_MS):
        return
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode, WhiteboardReentryPolicy
        email, gid = _owner_context()
        session = int(_shared_session_signature() or 0)
        if not email or gid <= 0 or session <= 0:
            return
        _shared_publication_generation = (int(_shared_publication_generation) + 1) & 0x7FFFFFFF
        generation = max(1, int(_shared_publication_generation))
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccounts()
        desired_slot = int(generation % int(_TEAM_PLAN_TX_SLOTS))
        committed_slots: set[int] = set()
        for intent in all_accounts.Intents:
            info = _tx_key_info(int(getattr(intent, "SkillID", 0) or 0))
            if (
                getattr(intent, "Active", False) and info is not None
                and int(info[1]) == int(_TX_COMMIT)
                and str(getattr(intent, "OwnerEmail", "") or "") == email
                and int(getattr(intent, "IsolationGroupID", 0) or 0) == int(gid)
                and int(getattr(intent, "ExpiresAtTick", 0) or 0) > now
            ):
                committed_slots.add(int(info[0]))
        # Never build an incomplete replacement in the slot holding the last
        # committed plan. Repeated PostLock failures therefore cannot erase the
        # last good generation before a replacement COMMIT exists.
        slot = desired_slot
        if slot in committed_slots:
            alternatives = [i for i in range(int(_TEAM_PLAN_TX_SLOTS)) if i not in committed_slots]
            if not alternatives:
                return
            slot = int(alternatives[generation % len(alternatives)])

        # Reuse only an unprotected generation slot. Older committed slots stay
        # alive until the new COMMIT succeeds, so readers can keep using the prior
        # plan while an update is incomplete.
        for intent in all_accounts.Intents:
            info = _tx_key_info(int(getattr(intent, "SkillID", 0) or 0))
            if (
                getattr(intent, "Active", False)
                and info is not None and int(info[0]) == slot
                and str(getattr(intent, "OwnerEmail", "") or "") == email
                and int(getattr(intent, "IsolationGroupID", 0) or 0) == int(gid)
            ):
                intent.reset()

        posted_slots: list[int] = []
        expires = now + int(_SHARED_PLAN_TTL_MS)
        def post(field: int, value: int) -> bool:
            idx = int(GLOBAL_CACHE.ShMem.PostLock(
                email, int(WhiteboardLockKind.SKILL_TARGET), _tx_key(slot, field), int(value),
                expires, gid, int(WhiteboardLockMode.SHARED), 32,
                int(WhiteboardReentryPolicy.OWNER_REENTRANT), int(WhiteboardClaimStrength.SOFT),
            ))
            if idx < 0:
                return False
            posted_slots.append(idx)
            return True

        members = tuple(sorted({int(v) for v in plan.member_ids if int(v or 0) > 0}))
        reason_code = int(_TEAM_PLAN_REASON_CODES.get(str(plan.reason), 0))
        mode_code = int(_TEAM_PLAN_MODE_CODES.get(str(plan.mode), 0))
        # Transaction equivalents of the legacy _TEAM_PLAN_VERSION_KEY and
        # _reason_key rows are _TX_VERSION and _TX_REASON within this generation.
        required = [
            (_TX_GEN, generation), (_TX_SESSION, session), (_TX_VERSION, max(1, int(plan.version or 0))),
            (_TX_GROUP, int(plan.group_anchor or 0)), (_TX_AOE, int(plan.aoe_anchor or 0)),
            (_TX_FOCUS, int(plan.focus_target or 0)), (_TX_MODE, mode_code),
            (_TX_REASON, reason_code), (_TX_MEMBER_COUNT, len(members)),
        ]
        if mode_code <= 0 or not members or any(not post(field, value) for field, value in required):
            _reset_slots(posted_slots)
            return
        for member in members:
            if not post(_TX_MEMBER, int(member)):
                _reset_slots(posted_slots)
                return
        # Commit is the only validity marker and is deliberately the final write.
        if not post(_TX_COMMIT, generation):
            _reset_slots(posted_slots)
            return

        # Stage 2.46 keeps the proven Stage-1.66 transaction schema intact. The
        # optional secondary damage anchor is a best-effort companion lease
        # written only *after* COMMIT. Readers accept it only when it belongs to
        # the same publisher/group and is a member of the committed packet.
        secondary = int(getattr(plan, "secondary_focus_target", 0) or 0)
        try:
            for intent in all_accounts.Intents:
                if (
                    getattr(intent, "Active", False)
                    and str(getattr(intent, "OwnerEmail", "") or "") == email
                    and int(getattr(intent, "IsolationGroupID", 0) or 0) == int(gid)
                    and int(getattr(intent, "SkillID", 0) or 0) == int(globals().get("_TEAM_PLAN_SECONDARY_KEY", 948105))
                ):
                    intent.reset()
            if secondary > 0:
                GLOBAL_CACHE.ShMem.PostLock(
                    email, int(WhiteboardLockKind.SKILL_TARGET), int(globals().get("_TEAM_PLAN_SECONDARY_KEY", 948105)),
                    int(secondary), expires, gid, int(WhiteboardLockMode.SHARED), 4,
                    int(WhiteboardReentryPolicy.OWNER_REENTRANT), int(WhiteboardClaimStrength.SOFT),
                )
        except Exception:
            pass

        # New generation is complete. Old protocol rows are now redundant and
        # can be reclaimed without ever exposing a partial plan as authoritative.
        keep = set(posted_slots)
        for idx, intent in enumerate(all_accounts.Intents):
            if idx in keep or not getattr(intent, "Active", False):
                continue
            if str(getattr(intent, "OwnerEmail", "") or "") != email or int(getattr(intent, "IsolationGroupID", 0) or 0) != int(gid):
                continue
            if _tx_key_info(int(getattr(intent, "SkillID", 0) or 0)) is not None:
                intent.reset()
        _last_shared_post_ms = now
        _last_shared_post_version = int(plan.version)
    except Exception:
        return


def _cache_complete_shared(plan: TeamCombatPlan, *, session: int) -> TeamCombatPlan:
    global _last_complete_shared_plan, _last_complete_shared_expires_ms, _last_complete_shared_session
    _last_complete_shared_plan = plan
    _last_complete_shared_expires_ms = int(plan.created_at_ms) + int(_SHARED_PLAN_TTL_MS)
    _last_complete_shared_session = int(session)
    return plan


def _last_valid_shared_fallback(now: int, session: int) -> TeamCombatPlan | None:
    if (
        int(session) > 0
        and int(_last_complete_shared_session) == int(session)
        and int(now) <= int(_last_complete_shared_expires_ms)
        and str(_last_complete_shared_plan.mode) != "none"
    ):
        if str(_last_complete_shared_plan.mode) in {"cleanup", "low_hp_finish"}:
            focus = int(_last_complete_shared_plan.focus_target or 0)
            if focus <= 0 or not _valid_alive(focus):
                return None
        return _last_complete_shared_plan
    return None


def _read_shared_plan() -> TeamCombatPlan | None:
    now = int(_now_ms())
    session = int(_shared_session_signature() or 0)
    try:
        from Py4GWCoreLib import GLOBAL_CACHE
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        _email, gid = _owner_context()
        if gid <= 0 or now <= 0 or session <= 0:
            return None
        rows: list[tuple[int, int, int, str, int, int]] = []
        for intent in GLOBAL_CACHE.ShMem.GetAllAccounts().Intents:
            if not getattr(intent, "Active", False):
                continue
            if int(getattr(intent, "KindID", 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET):
                continue
            if int(getattr(intent, "IsolationGroupID", 0) or 0) != int(gid):
                continue
            expires = int(getattr(intent, "ExpiresAtTick", 0) or 0)
            if expires <= now:
                continue
            key = int(getattr(intent, "SkillID", 0) or 0)
            info = _tx_key_info(key)
            if info is None:
                continue
            posted = int(getattr(intent, "PostedAtTick", 0) or 0)
            rows.append((posted, int(info[0]), int(info[1]), str(getattr(intent, "OwnerEmail", "") or ""), int(getattr(intent, "TargetAgentID", 0) or 0), expires))

        commits = sorted((r for r in rows if r[2] == _TX_COMMIT), key=lambda r: r[0], reverse=True)
        for commit in commits:
            commit_posted, slot, _field, owner, generation, _expires = commit
            if generation <= 0 or not _publisher_owner_is_current_leader(owner, gid):
                continue
            same = [r for r in rows if r[1] == slot and r[3] == owner]
            by_field: dict[int, list[tuple[int, int, int, str, int, int]]] = {}
            for row in same:
                by_field.setdefault(int(row[2]), []).append(row)
            def one(field: int):
                vals = by_field.get(int(field), [])
                return vals[0] if len(vals) == 1 else None
            gen_row = one(_TX_GEN); session_row = one(_TX_SESSION); version_row = one(_TX_VERSION)
            group_row = one(_TX_GROUP); aoe_row = one(_TX_AOE); focus_row = one(_TX_FOCUS)
            mode_row = one(_TX_MODE); reason_row = one(_TX_REASON); count_row = one(_TX_MEMBER_COUNT); commit_row = one(_TX_COMMIT)
            required_rows = (gen_row, session_row, version_row, group_row, aoe_row, focus_row, mode_row, reason_row, count_row, commit_row)
            if any(row is None for row in required_rows):
                continue
            assert gen_row and session_row and version_row and group_row and aoe_row and focus_row and mode_row and reason_row and count_row and commit_row
            if int(gen_row[4]) != int(generation) or int(commit_row[4]) != int(generation):
                continue
            if int(session_row[4]) != int(session):
                continue
            posted_values = [int(row[0]) for row in required_rows if row is not None]
            member_rows = by_field.get(_TX_MEMBER, [])
            posted_values.extend(int(row[0]) for row in member_rows)
            if not posted_values or max(posted_values) - min(posted_values) > int(_SHARED_PLAN_POST_SKEW_MS):
                continue
            expected_members = int(count_row[4])
            raw_members = tuple(sorted({int(row[4]) for row in member_rows if int(row[4]) > 0}))
            if expected_members <= 0 or len(raw_members) != expected_members or len(member_rows) != expected_members:
                continue
            mode = _TEAM_PLAN_MODE_BY_CODE.get(int(mode_row[4]), "")
            if not mode:
                continue
            focus_id = int(focus_row[4])
            if str(mode) in {"cleanup", "low_hp_finish"} and (focus_id <= 0 or not _valid_alive(focus_id)):
                # Never keep or resurrect a completed execute through a stale
                # shared-memory lease. Death is the authoritative handoff.
                continue
            # Legacy _TEAM_PLAN_REASON rows are now generation-bound in _TX_REASON.
            version = int(version_row[4])
            reason = _TEAM_PLAN_REASON_BY_CODE.get(int(reason_row[4]), "shared_whiteboard_lease")
            members = tuple(int(v) for v in raw_members if _valid_alive(int(v)))
            if not members:
                continue
            secondary = 0
            try:
                for intent in GLOBAL_CACHE.ShMem.GetAllAccounts().Intents:
                    if not getattr(intent, "Active", False):
                        continue
                    if str(getattr(intent, "OwnerEmail", "") or "") != str(owner):
                        continue
                    if int(getattr(intent, "IsolationGroupID", 0) or 0) != int(gid):
                        continue
                    if int(getattr(intent, "KindID", 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET):
                        continue
                    if int(getattr(intent, "SkillID", 0) or 0) != int(globals().get("_TEAM_PLAN_SECONDARY_KEY", 948105)):
                        continue
                    if int(getattr(intent, "ExpiresAtTick", 0) or 0) <= now:
                        continue
                    candidate_secondary = int(getattr(intent, "TargetAgentID", 0) or 0)
                    if candidate_secondary in set(int(v) for v in members) and candidate_secondary != int(focus_row[4]):
                        secondary = int(candidate_secondary)
                        break
            except Exception:
                secondary = 0
            try:
                plan = TeamCombatPlan(
                    int(version), int(commit_posted), str(mode), int(group_row[4]),
                    int(aoe_row[4]), int(focus_row[4]), members, str(reason), "shared",
                    int(secondary),
                )
            except TypeError:
                # Historical Stage-1.66 tests inject the original 9-field plan
                # stub. Keep the established transaction reader compatible; the
                # companion secondary lease is additive only.
                plan = TeamCombatPlan(
                    int(version), int(commit_posted), str(mode), int(group_row[4]),
                    int(aoe_row[4]), int(focus_row[4]), members, str(reason), "shared",
                )
            return _cache_complete_shared(plan, session=session)
        return _last_valid_shared_fallback(now, session)
    except Exception:
        return _last_valid_shared_fallback(now, session)


def get_team_combat_plan(*, filter_range: float = DEFAULT_FILTER_RANGE, minimum_enemies: int = 2, consumer_role: str = "") -> TeamCombatPlan:
    """Read one immutable combat plan snapshot.

    Consumers must not independently resolve an anchor and then re-query members;
    doing so allowed a canonical packet to disappear between two reads.  The party
    leader also broadcasts the same snapshot as a short shared-memory lease so
    eSurge clients and owned RoJ heroes converge on one combat group/version.
    """
    global _cached_plan, _cached_plan_tick, _plan_geometry_xy, _plan_geometry_player_xy
    now = int(_now_ms())

    shared = _read_shared_plan()
    if shared is not None and not _is_shared_publisher():
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_state_event(
                "TEAM_FOCUS_DECISION",
                signature_fields=("version", "mode", "group_anchor", "focus_target", "secondary_focus_target", "packet_members"),
                min_interval_ms=3000, heartbeat_ms=8000,
                version=int(shared.version), mode=str(shared.mode), target_id=int(_plan_target(shared)),
                group_anchor=int(shared.group_anchor), aoe_anchor=int(shared.aoe_anchor),
                focus_target=int(shared.focus_target), secondary_focus_target=int(getattr(shared, "secondary_focus_target", 0) or 0), packet_size=int(len(shared.member_ids)),
                packet_members=",".join(str(v) for v in shared.member_ids),
                consumer_role=str(consumer_role or "party"), plan_source="shared",
                switch_reason=str(shared.reason),
                policy="stage1_57_single_snapshot_shared_combat_group",
            )
        except Exception:
            pass
        return shared

    if now > 0 and now - int(_cached_plan_tick) < int(_PLAN_CACHE_MS):
        return _cached_plan

    # Native position reads dominate the pairwise cluster scans in large mobs.
    # Snapshot only within one recomputation; never cache beyond this 45-ms
    # plan boundary, never keep stale agent IDs across encounters or maps.
    _plan_geometry_xy = {}
    try:
        _plan_geometry_player_xy = Player.GetXY()
    except Exception:
        _plan_geometry_player_xy = None
    try:
        candidate = _raw_candidate(filter_range=float(filter_range), minimum_enemies=int(minimum_enemies), consumer_role=str(consumer_role or "party"))
        old = _cached_plan
        plan = _stabilize_candidate(candidate, filter_range=float(filter_range))
    finally:
        _plan_geometry_xy = None
        _plan_geometry_player_xy = None
    _cached_plan = plan
    _cached_plan_tick = now
    _log_switch(old, plan, filter_range=float(filter_range))
    _post_shared_plan(plan)

    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_state_event(
            "TEAM_FOCUS_DECISION",
            signature_fields=("version", "mode", "group_anchor", "focus_target", "secondary_focus_target", "packet_members"),
            min_interval_ms=3000, heartbeat_ms=8000,
            version=int(plan.version), mode=str(plan.mode),
            target_id=int(_plan_target(plan)), group_anchor=int(plan.group_anchor),
            aoe_anchor=int(plan.aoe_anchor), focus_target=int(plan.focus_target),
            secondary_focus_target=int(getattr(plan, "secondary_focus_target", 0) or 0), packet_size=int(len(plan.member_ids)),
            packet_members=",".join(str(v) for v in plan.member_ids),
            consumer_role=str(consumer_role or "party"), plan_source=str(plan.source),
            switch_reason=str(plan.reason),
            policy="stage1_57_single_snapshot_shared_combat_group",
        )
    except Exception:
        pass
    return plan


def get_team_primary_target(*, filter_range: float = DEFAULT_FILTER_RANGE, minimum_enemies: int = 1, consumer_role: str = "") -> int:
    """Return the one shared kill target, distinct from the AoE packet center.

    Stage 2.44 keeps ``aoe_anchor`` available for geometry-sensitive skills but
    gives direct-damage consumers a stable target that all accounts can burn
    together until death.
    """
    plan = get_team_combat_plan(
        filter_range=float(filter_range), minimum_enemies=int(minimum_enemies),
        consumer_role=str(consumer_role or "primary_focus"),
    )
    target = int(plan.focus_target or 0)
    if target <= 0:
        target = int(plan.aoe_anchor or plan.group_anchor or 0)
    return int(target) if _valid(target, filter_range) else 0


def get_team_damage_anchors(*, filter_range: float = DEFAULT_FILTER_RANGE, minimum_enemies: int = 1, consumer_role: str = "") -> tuple[int, int]:
    """Return the shared primary/secondary damage anchors for Stage 2.46.

    <=10% cleanup intentionally collapses back to one hard focus.
    """
    plan = get_team_combat_plan(
        filter_range=float(filter_range), minimum_enemies=int(minimum_enemies),
        consumer_role=str(consumer_role or "damage_pair"),
    )
    primary = int(plan.focus_target or 0)
    secondary = int(getattr(plan, "secondary_focus_target", 0) or 0)
    if str(plan.mode) in {"cleanup", "low_hp_finish", "st_defense", "krummrinde"}:
        secondary = 0
    if primary <= 0 or not _valid(primary, filter_range):
        primary = 0
    if secondary <= 0 or secondary == primary or not _valid(secondary, filter_range):
        secondary = 0
    return int(primary), int(secondary)


def get_team_cluster_anchor(*, filter_range: float = DEFAULT_FILTER_RANGE, minimum_enemies: int = 2, consumer_role: str = "") -> int:
    plan = get_team_combat_plan(filter_range=float(filter_range), minimum_enemies=int(minimum_enemies), consumer_role=str(consumer_role or "party"))
    target = _plan_target(plan)
    return int(target) if _valid(target, filter_range) else 0


def get_team_cluster_members(anchor_agent_id: int, *, radius: float = DEFAULT_CLUSTER_RADIUS, filter_range: float = DEFAULT_FILTER_RANGE) -> list[int]:
    plan = get_team_combat_plan(filter_range=float(filter_range), minimum_enemies=2, consumer_role="member_read")
    anchor = int(anchor_agent_id or 0)
    plan_ids = {int(plan.group_anchor), int(plan.aoe_anchor), int(plan.focus_target), *[int(v) for v in plan.member_ids]}
    if anchor > 0 and anchor in plan_ids:
        # Group identity is shared, but this legacy API still means "members hit
        # by an AoE centered on anchor". Keep the radius filter so damage models
        # do not accidentally treat a connected fight as one giant packet.
        return [
            int(v) for v in plan.member_ids
            if _valid(int(v), filter_range) and _distance_between(anchor, int(v)) <= float(radius)
        ]
    if not _valid(anchor, filter_range):
        return []
    try:
        arr = AgentArray.GetEnemyArray()
        arr = AgentArray.Filter.ByDistance(arr, Agent.GetXY(anchor), float(radius))
        return sorted({int(eid) for eid in arr or [] if _valid(int(eid), filter_range)} | {anchor})
    except Exception:
        return [anchor]


def pick_lamentation_target(*, cleanup_dangerous_only: bool = True) -> int:
    plan = get_team_combat_plan()
    anchor = _plan_target(plan)
    members = [int(v) for v in plan.member_ids if _valid(int(v))]
    if anchor <= 0 or not members:
        return 0
    if len(members) <= 1 and cleanup_dangerous_only and not is_support_or_caster(anchor):
        return 0
    members.sort(key=lambda eid: (-count_adjacent_enemies(eid), -int(is_support_or_caster(eid)), _distance(eid), eid))
    return members[0]


def pick_unhexed_blood_bond_target() -> int:
    plan = get_team_combat_plan()
    candidates = []
    for eid in plan.member_ids:
        if not _valid(int(eid)):
            continue
        try:
            if Agent.IsHexed(int(eid)):
                continue
        except Exception:
            pass
        candidates.append(int(eid))
    if not candidates:
        return 0
    martial_ids = {
        int(getattr(Profession.Warrior, "value", Profession.Warrior)),
        int(getattr(Profession.Ranger, "value", Profession.Ranger)),
        int(getattr(Profession.Assassin, "value", Profession.Assassin)),
        int(getattr(Profession.Dervish, "value", Profession.Dervish)),
        int(getattr(Profession.Paragon, "value", Profession.Paragon)),
    }
    def rank(eid: int):
        p, _s = enemy_professions(eid)
        return (-int(is_support_or_caster(eid)), -int(p in martial_ids), -count_adjacent_enemies(eid), _distance(eid), eid)
    candidates.sort(key=rank)
    return candidates[0]
