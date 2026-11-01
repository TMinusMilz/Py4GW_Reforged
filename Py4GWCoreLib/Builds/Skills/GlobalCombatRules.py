"""Global eSurge roster; optional effect-verified DoA combat exceptions.

Combat-only predicates. No route, flags, waypoint, quest or item automation.
Never guess an environment merely from Map 474, because its four regions
share one map. An unobservable effect intentionally disables its special case.
"""
from __future__ import annotations

from Py4GWCoreLib import Agent, GLOBAL_CACHE, Map, Party, Player, Skill


def _skill_id(name: str) -> int:
    try:
        return int(Skill.GetID(name) or 0)
    except Exception:
        return 0


def _effect_active(agent_id: int, name: str) -> bool:
    sid = _skill_id(name)
    if sid <= 0 or agent_id <= 0:
        return False
    try:
        fx = GLOBAL_CACHE.Effects
        entries = list(fx.GetEffects(int(agent_id)) or [])
        entries.extend(list(fx.GetBuffs(int(agent_id)) or []))
        return any(int(getattr(e, 'skill_id', 0) or 0) == sid for e in entries)
    except Exception:
        return False


def doa_hm_effect_active(name: str) -> bool:
    try:
        return bool(int(Map.GetMapID() or 0) == 474 and Party.IsHardMode()
                    and _effect_active(int(Player.GetAgentID() or 0), name))
    except Exception:
        return False


def displacement_is_hazardous() -> bool:
    """Stygian Veil: blocks can inflict damage; proof requires visible effect."""
    return doa_hm_effect_active('Demonic_Miasma')


def torment_execute_active(enemy_id: int) -> bool:
    """Only prioritize a *confirmed* active Call effect; no HP-based guess.

    Current APIs may not expose the effect. In that case this safely returns
    False and the shared generic kill focus remains unchanged.
    """
    try:
        if int(Map.GetMapID() or 0) != 474:
            return False
        return bool(Agent.IsValid(int(enemy_id)) and Agent.IsAlive(int(enemy_id))
                    and _effect_active(int(enemy_id), 'Call_to_the_Torment'))
    except Exception:
        return False


def doa_target_bonus(enemy_id: int) -> int:
    """Only names observed inside DoA HM; prioritizes kills, never pulls.

    Names are useful when the environment effect API lacks region visibility.
    Unknown/localized enemy names receive no invented classification.
    """
    try:
        if int(Map.GetMapID() or 0) != 474 or not Party.IsHardMode():
            return 0
        name = str(Agent.GetName(int(enemy_id)) or '').strip().lower()
    except Exception:
        return 0
    if 'margonite anur ki' in name:
        return 125
    if 'margonite anur ruk' in name:
        return 95
    if 'margonite anur dabi' in name:
        return 90
    if 'tortureweb dryder' in name:
        return 85
    if 'margonite anur kaya' in name:
        return 78
    if 'margonite anur su' in name:
        return 72
    return 0
