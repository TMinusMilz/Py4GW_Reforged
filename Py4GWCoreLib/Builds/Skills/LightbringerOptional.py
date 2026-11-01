"""Optional DoA Lightbringer support for user-equipped skills only.

Does not load, swap, or equip any skill. Does not select fresh pulls.
The frozen Keystone baseline has no gameplay dependency on this module
unless one of the two optional skills is actually present in the current bar.
"""
from math import hypot
from Py4GWCoreLib import Agent, AgentArray, Map, Player, Range
from Py4GWCoreLib.Skill import Skill

# IDs are the stable Guild Wars skill IDs. Resolve by name if available.
def _resolve_skill_id(name, fallback):
    try:
        return int(Skill.GetID(name) or fallback)
    except Exception:
        return int(fallback)


GAZE_ID = _resolve_skill_id("Lightbringers_Gaze", 1814)
SIGNET_ID = _resolve_skill_id("Lightbringer_Signet", 1815)
DOA_MAP_ID = 474


def _log(event, **fields):
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(str(event), **fields)
    except Exception:
        pass


def _is_doa():
    try:
        return int(Map.GetMapID() or 0) == DOA_MAP_ID
    except Exception:
        return False


def _equipped_ready(build, skill_id):
    try:
        return bool(skill_id > 0 and build.IsSkillEquipped(int(skill_id))
                    and build.CanCastSkillID(int(skill_id)))
    except Exception:
        return False


def _engaged_packet(role):
    """Only query the team's already-selected encounter; never new pulls."""
    try:
        from Py4GWCoreLib.Builds.Skills.TeamCombatFocus import (
            get_team_cluster_anchor, get_team_cluster_members)
        anchor = int(get_team_cluster_anchor(
            filter_range=float(Range.Spellcast.value),
            minimum_enemies=2, consumer_role=str(role)) or 0)
        if anchor <= 0:
            return []
        raw = get_team_cluster_members(
            anchor, radius=float(Range.Area.value),
            filter_range=float(Range.Spellcast.value)) or []
        return sorted(set(int(aid) for aid in raw
                          if int(aid or 0) > 0
                          and Agent.IsValid(int(aid))
                          and Agent.IsAlive(int(aid))))
    except Exception:
        return []


def _gaze_target(min_enemies, role):
    members = _engaged_packet(role)
    if len(members) < int(min_enemies):
        return (0, 0)
    positions = {}
    for agent_id in members:
        try:
            x, y = Agent.GetXY(agent_id)
            if float(Agent.GetHealth(agent_id)) <= 0.28:
                continue
            positions[agent_id] = (float(x), float(y))
        except Exception:
            continue
    if not positions:
        return (0, 0)
    radius = float(Range.Area.value)
    # Compare all live packet members, including low-HP neighbors, but never
    # cast *on* an almost dead mob. The first target is the only interrupted one.
    allpos = {}
    for agent_id in members:
        try:
            x,y = Agent.GetXY(agent_id)
            allpos[agent_id] = (float(x),float(y))
        except Exception:
            pass
    ranks = []
    for aid, (x,y) in positions.items():
        hits=sum(1 for xx,yy in allpos.values()
                 if hypot(x-xx,y-yy) <= radius)
        if hits >= int(min_enemies):
            try:
                from Py4GWCoreLib.Builds.Skills import EnemyKnowledge
                scores = EnemyKnowledge.role_scores(int(aid))
                importance = (int(scores.get('healer',0)) * 3
                              + int(scores.get('support',0)) * 2
                              + int(scores.get('caster',0)))
            except Exception:
                importance=0
            ranks.append((min(6,hits),importance,aid))
    if not ranks:
        return (0,0)
    hits, _, target=max(ranks)
    return (int(target),int(hits))


def _demon_within_area():
    # DoA monsters are lightbringer targets. Nearby summoned spirits are not;
    # this check is a conservative area probe, not a guaranteed success proof.
    try:
        x, y=Player.GetXY()
        for eid in AgentArray.GetEnemyArray() or []:
            eid=int(eid or 0)
            if eid<=0 or not Agent.IsValid(eid) or not Agent.IsAlive(eid):
                continue
            ex,ey=Agent.GetXY(eid)
            if hypot(float(ex)-float(x),float(ey)-float(y)) <= float(Range.Area.value):
                return True
    except Exception:
        pass
    return False


def try_gaze(build, *, role, min_enemies):
    """One opportunistic AoE request; existing rotations must run FIRST."""
    if not _is_doa() or not build.IsInAggro() or not _equipped_ready(build, GAZE_ID):
        return False
    target, count = _gaze_target(min_enemies, role)
    if target <= 0:
        return False
    try:
        did = yield from build.CastSkillIDAndRestoreTarget(
            skill_id=GAZE_ID, target_agent_id=target,
            log=False, aftercast_delay=250)
    except Exception:
        return False
    if did:
        _log('LIGHTBRINGER_GAZE_REQUESTED', role=role,
             target_id=target, clustered_foes=count,
             policy='optional_only_after_core_rotation_doa_no_new_pull')
    return bool(did)


def try_signet(build, *, role, energy_threshold=0.40, allow_adrenaline=False):
    """Use only when in demon area and energy-starved, never blindly on recharge."""
    if not _is_doa() or not build.IsInAggro() or not _equipped_ready(build, SIGNET_ID):
        return False
    if not _demon_within_area():
        return False
    try:
        energy = float(Agent.GetEnergy(int(Player.GetAgentID() or 0)))
    except Exception:
        return False
    if energy > float(energy_threshold):
        # An HR may instead use the five adrenal strikes when SY is not ready.
        if not allow_adrenaline:
            return False
        try:
            sy_ids = (int(Skill.GetID('Save_Yourselves_luxon') or 0),
                      int(Skill.GetID('Save_Yourselves_kurzick') or 0))
            equipped_sy = [sid for sid in sy_ids if sid>0 and build.IsSkillEquipped(sid)]
            if not equipped_sy or any(build.CanCastSkillID(sid) for sid in equipped_sy):
                return False
        except Exception:
            return False
    try:
        did=yield from build.CastSkillID(skill_id=SIGNET_ID,log=False,aftercast_delay=250)
    except Exception:
        return False
    if did:
        _log('LIGHTBRINGER_SIGNET_REQUESTED',role=role,
             energy_before=round(energy,3),
             policy='optional_doa_only_near_demon_energy_or_SY_battery')
    return bool(did)
