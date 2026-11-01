"""Crash-safe enemy knowledge for coordinated HeroAI builds.

This module combines stable live information (map, mode, model id, level,
profession and observed casts) with small static role profiles.  It does not
hook native damage/combat events and therefore avoids the crash-prone telemetry
path used by earlier experiments.

The knowledge is deliberately advisory: every lookup fails open and returns a
neutral score when the runtime cannot provide a value.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from Py4GWCoreLib import Profession
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.Map import Map
from Py4GWCoreLib.Party import Party
from Py4GWCoreLib.Skill import Skill
from Py4GWCoreLib.Builds.Knowledge import OfflineKnowledge

RETENTION_MS = 180000


def _now_ms() -> int:
    try:
        import PySystem
        return int(PySystem.get_tick_count64() or 0)
    except Exception:
        try:
            import time
            return int(time.monotonic() * 1000.0)
        except Exception:
            return 0


def _skill_ids(names: tuple[str, ...]) -> frozenset[int]:
    result: set[int] = set()
    for name in names:
        try:
            sid = int(Skill.GetID(name) or 0)
        except Exception:
            sid = 0
        if sid > 0:
            result.add(sid)
    return frozenset(result)


HEAL_SKILLS = _skill_ids((
    "Word_of_Healing", "Heal_Party", "Healing_Burst", "Patient_Spirit",
    "Dwaynas_Kiss", "Dwayna's_Kiss", "Orison_of_Healing", "Infuse_Health",
    "Spirit_Light", "Mend_Body_and_Soul", "Soothing_Memories",
    "Protective_Was_Kaolai", "Recuperation", "Life", "Preservation",
))
PROT_SKILLS = _skill_ids((
    "Protective_Spirit", "Spirit_Bond", "Aegis", "Guardian",
    "Shield_of_Absorption", "Life_Sheath", "Reversal_of_Fortune",
    "Shield_Guardian", "Weapon_of_Warding", "Shelter", "Union",
    "Displacement", "Protective_Was_Kaolai",
))
REZ_SKILLS = _skill_ids((
    "Resurrection_Signet", "Resurrection_Chant", "Restore_Life", "Rebirth",
    "Flesh_of_My_Flesh", "Death_Pact_Signet", "Renew_Life",
    "Light_of_Dwayna", "Signet_of_Return", "Unyielding_Aura",
))
DANGEROUS_OFFENSE_SKILLS = _skill_ids((
    "Panic", "Energy_Surge", "Mistrust", "Cry_of_Frustration", "Backfire",
    "Shame", "Diversion", "Meteor_Shower", "Savannah_Heat", "Searing_Heat",
    "Maelstrom", "Deep_Freeze", "Earthquake", "Churning_Earth", "Sandstorm",
    "Ray_of_Judgment", "Spiteful_Spirit", "Mark_of_Pain", "Barbs",
    "Spoil_Victor", "Putrid_Explosion",
))


@dataclass(slots=True)
class EnemyRecord:
    model_id: int = 0
    name: str = ""
    level: int = 0
    primary: int = 0
    secondary: int = 0
    map_id: int = 0
    hard_mode: bool = False
    observed_skills: set[int] = field(default_factory=set)
    heal_casts: int = 0
    prot_casts: int = 0
    rez_casts: int = 0
    dangerous_casts: int = 0
    total_casts: int = 0
    finished_casts: int = 0
    stopped_casts: int = 0
    interrupted_casts: int = 0
    last_cast_skill_id: int = 0
    last_cast_seen_ms: int = 0
    last_seen_ms: int = 0
    generation: int = 1


_RECORDS: dict[int, EnemyRecord] = {}
_MODEL_MEMORY: dict[tuple[int, int, bool], set[int]] = {}
_LAST_OUTCOME_SYNC_MS: int = 0
_LAST_CONTEXT: tuple[int, bool] = (0, False)
_LOGGED_PROFILE_HASH: dict[int, int] = {}
_LOGGED_PROFILE_TICK: dict[int, int] = {}
_LOGGED_OFFLINE_HITS: set[tuple[str, int, int]] = set()
PROFILE_LOG_MIN_INTERVAL_MS = 8000
_PROCESSED_OUTCOMES: set[tuple[int, int, int, int, str]] = set()
_SEEN_CAST_SEQUENCES: set[tuple[int, int, int, int, int]] = set()
_AGENT_GENERATIONS: dict[int, int] = {}
_RETIRED_AGENT_IDS: set[int] = set()  # legacy marker; Stage 18 no longer treats range loss as despawn
_RETIRED_IDENTITIES: dict[int, tuple[int, int, int, str]] = {}
# Legacy cumulative marker: exp36_stage14_e17b_recycled_agent_id_new_generation
_LAST_SESSION_EPOCH: int = 0

_HEALER_NAME_TOKENS = ("monk", "priest", "healer", "mender", "shaman", "ritualist", "spirit shepherd")
_SUPPORT_NAME_TOKENS = ("protector", "guardian", "binder", "chanter", "keeper", "caretaker")
_CASTER_NAME_TOKENS = ("mesmer", "elementalist", "necromancer", "sorcerer", "mage", "warlock", "seeker")


def _profession_ids() -> tuple[int, int, int, int, int, int]:
    def val(p) -> int:
        try:
            return int(getattr(p, "value", p) or 0)
        except Exception:
            return 0
    return (
        val(Profession.Monk), val(Profession.Ritualist), val(Profession.Mesmer),
        val(Profession.Elementalist), val(Profession.Necromancer),
        val(Profession.Paragon),
    )


def _safe_context() -> tuple[int, bool]:
    try:
        map_id = int(Map.GetMapID() or 0)
    except Exception:
        map_id = 0
    try:
        hard_mode = bool(Party.IsHardMode())
    except Exception:
        hard_mode = False
    return map_id, hard_mode


def _safe_static(agent_id: int) -> EnemyRecord:
    aid = int(agent_id or 0)
    map_id, hard_mode = _safe_context()
    try:
        model_id = int(Agent.GetModelID(aid) or 0)
    except Exception:
        model_id = 0
    try:
        name = str(Agent.GetNameByID(aid) or "")
    except Exception:
        name = ""
    try:
        level = int(Agent.GetLevel(aid) or 0)
    except Exception:
        level = 0
    try:
        p, s = Agent.GetProfessions(aid)
        primary = int(getattr(p, "value", p) or 0)
        secondary = int(getattr(s, "value", s) or 0)
    except Exception:
        primary = secondary = 0
    return EnemyRecord(
        model_id=model_id, name=name, level=level, primary=primary,
        secondary=secondary, map_id=map_id, hard_mode=hard_mode,
        last_seen_ms=_now_ms(),
    )


def _ensure_session_epoch() -> int:
    """Reset agent-id keyed learning when the actual instance changes."""
    global _LAST_SESSION_EPOCH
    try:
        from Py4GWCoreLib.Builds.Skills import CombatSession
        epoch = int(CombatSession.refresh().epoch or 0)
    except Exception:
        epoch = 0
    if epoch > 0 and epoch != int(_LAST_SESSION_EPOCH or 0):
        _LAST_SESSION_EPOCH = int(epoch)
        _RECORDS.clear()
        _MODEL_MEMORY.clear()
        _LOGGED_PROFILE_HASH.clear()
        _LOGGED_PROFILE_TICK.clear()
        _PROCESSED_OUTCOMES.clear()
        _SEEN_CAST_SEQUENCES.clear()
        _AGENT_GENERATIONS.clear()
        _RETIRED_AGENT_IDS.clear()
        _RETIRED_IDENTITIES.clear()
    return int(epoch)


def _identity_signature(agent_id: int) -> tuple[int, int, int, str]:
    """Return the strongest cheap identity fields currently exposed for an agent."""
    aid = int(agent_id or 0)
    try:
        model_id = int(Agent.GetModelID(aid) or 0)
    except Exception:
        model_id = 0
    try:
        primary, secondary = Agent.GetProfessionIDs(aid)
        primary_id = int(getattr(primary, "value", primary) or 0)
        secondary_id = int(getattr(secondary, "value", secondary) or 0)
    except Exception:
        primary_id = secondary_id = 0
    try:
        name = str(Agent.GetNameByID(aid) or "")
    except Exception:
        name = ""
    return int(model_id), int(primary_id), int(secondary_id), str(name)


def _identity_tuple_changed(previous: tuple[int, int, int, str], current: tuple[int, int, int, str]) -> bool:
    old_model, old_primary, _old_secondary, old_name = previous
    model_id, primary_id, _secondary_id, name = current
    if int(old_model or 0) > 0 and int(model_id or 0) > 0:
        return int(old_model) != int(model_id)
    if int(old_model or 0) <= 0 and int(model_id or 0) <= 0:
        if int(old_primary or 0) > 0 and int(primary_id or 0) > 0 and int(old_primary) != int(primary_id):
            return True
        if str(old_name or "") and str(name or "") and str(old_name) != str(name):
            return True
    return False


def _record_identity_changed(rec: EnemyRecord, current: tuple[int, int, int, str]) -> bool:
    return _identity_tuple_changed(
        (int(rec.model_id or 0), int(rec.primary or 0), int(rec.secondary or 0), str(rec.name or "")),
        current,
    )


def agent_generation(agent_id: int) -> int:
    """Return the local generation for a recycled AgentID inside this session."""
    aid = int(agent_id or 0)
    if aid <= 0:
        return 0
    rec = _RECORDS.get(aid)
    if rec is not None:
        return max(1, int(rec.generation or 1))
    return max(1, int(_AGENT_GENERATIONS.get(aid, 1) or 1))


def agent_identity_key(agent_id: int) -> str:
    rec = observe(int(agent_id), 0)
    return f"a{int(agent_id or 0)}:g{int(rec.generation or 1)}:m{int(rec.model_id or 0)}:p{int(rec.primary or 0)}"


def observe(agent_id: int, casting_skill_id: int = 0) -> EnemyRecord:
    aid = int(agent_id or 0)
    if aid <= 0:
        return EnemyRecord()
    epoch = _ensure_session_epoch()
    rec = _RECORDS.get(aid)
    current_identity = _identity_signature(aid)
    if rec is None:
        previous_generation = max(1, int(_AGENT_GENERATIONS.get(aid, 1) or 1))
        retired_identity = _RETIRED_IDENTITIES.get(aid)
        # Stage 18 / E17d: absence from CombatSense's distance-filtered array is
        # not a despawn signal.  Only a real static identity change (model or,
        # when model is unavailable, profession/name) advances generation.
        reused_with_changed_identity = bool(
            retired_identity is not None
            and _identity_tuple_changed(tuple(retired_identity), tuple(current_identity))
        )
        next_generation = int(previous_generation + 1) if reused_with_changed_identity else int(previous_generation)
        rec = _safe_static(aid)
        rec.generation = max(1, int(next_generation))
        _AGENT_GENERATIONS[aid] = int(rec.generation)
        _RETIRED_AGENT_IDS.discard(aid)
        _RETIRED_IDENTITIES.pop(aid, None)
        _RECORDS[aid] = rec
        if reused_with_changed_identity:
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event(
                    "ENEMY_AGENT_GENERATION_CHANGED",
                    agent_id=aid, generation=int(rec.generation),
                    old_model_id=int(retired_identity[0] or 0), new_model_id=int(rec.model_id or 0),
                    old_name=str(retired_identity[3] or ""), new_name=str(rec.name or ""),
                    session_epoch=int(epoch),
                    reason="retained_identity_changed_after_record_retention_prune",
                    policy="exp36_stage18_e17d_identity_change_only_no_range_flapping",
                )
            except Exception:
                pass
    elif _record_identity_changed(rec, current_identity):
        old_model = int(rec.model_id or 0)
        old_name = str(rec.name or "")
        next_generation = max(1, int(_AGENT_GENERATIONS.get(aid, rec.generation or 1) or 1)) + 1
        _AGENT_GENERATIONS[aid] = int(next_generation)
        rec = _safe_static(aid)
        rec.generation = int(next_generation)
        _RECORDS[aid] = rec
        # Remove cast-sequence rows belonging to the previous occupant of this
        # AgentID; otherwise a recycled id using the same skill/start tick can
        # inherit stale learning inside one long elite-area instance.
        for seq in list(_SEEN_CAST_SEQUENCES):
            if len(seq) >= 3 and int(seq[1]) == aid:
                _SEEN_CAST_SEQUENCES.discard(seq)
        _LOGGED_PROFILE_HASH.pop(aid, None)
        _LOGGED_PROFILE_TICK.pop(aid, None)
        try:
            from Py4GWCoreLib.Builds.Skills import CombatDebug
            CombatDebug.log_event(
                "ENEMY_AGENT_GENERATION_CHANGED",
                agent_id=aid, generation=int(next_generation),
                old_model_id=int(old_model), new_model_id=int(rec.model_id or 0),
                old_name=str(old_name), new_name=str(rec.name or ""),
                session_epoch=int(epoch),
                policy="exp36_stage18_e17d_direct_identity_change_new_generation",
            )
        except Exception:
            pass
    now = _now_ms()
    rec.last_seen_ms = now
    sid = int(casting_skill_id or 0)
    if sid > 0:
        previous_sid = int(rec.last_cast_skill_id or 0)
        previous_seen = int(rec.last_cast_seen_ms or 0)
        try:
            from Py4GWCoreLib.Builds.Skills import ReforgedSupport
            start_tick = int(ReforgedSupport.get_cast_start_tick(aid, sid) or 0)
        except Exception:
            start_tick = 0

        if start_tick > 0:
            sequence = (int(epoch), aid, int(rec.generation or 1), sid, start_tick)
            is_new_cast = sequence not in _SEEN_CAST_SEQUENCES
            _SEEN_CAST_SEQUENCES.add(sequence)
        else:
            # Polling fallback: update last-seen on every frame.  A long cast can
            # therefore never be re-counted merely because 650 ms elapsed.
            gap_ms = max(0, int(now) - int(previous_seen)) if previous_seen else 999999
            is_new_cast = sid != previous_sid or gap_ms > 500

        rec.observed_skills.add(sid)
        if is_new_cast:
            rec.total_casts += 1
            if sid in HEAL_SKILLS:
                rec.heal_casts += 1
            if sid in PROT_SKILLS:
                rec.prot_casts += 1
            if sid in REZ_SKILLS:
                rec.rez_casts += 1
            if sid in DANGEROUS_OFFENSE_SKILLS:
                rec.dangerous_casts += 1
            if rec.model_id > 0:
                _MODEL_MEMORY.setdefault((rec.map_id, rec.model_id, rec.hard_mode), set()).add(sid)
        rec.last_cast_skill_id = sid
        rec.last_cast_seen_ms = now

    _log_offline_first_contact(rec, aid)

    # Stage 13 can install EncounterPolicy; Stage 12 fails open with no module.
    try:
        from Py4GWCoreLib.Builds.Skills import EncounterPolicy
        EncounterPolicy.observe_enemy(
            aid, model_id=int(rec.model_id), name=str(rec.name or ""),
            casting_skill_id=int(sid),
        )
    except Exception:
        pass
    return rec



def _log_offline_first_contact(rec: EnemyRecord, agent_id: int) -> None:
    """Emit one proof marker for an exact offline map/model hit per identity."""
    try:
        if int(rec.model_id or 0) <= 0 or not OfflineKnowledge.has_exact_enemy_profile(rec.model_id, rec.map_id):
            return
        try:
            from Py4GWCoreLib.Builds.Skills import CombatSession
            session_key = str(CombatSession.current_key() or "")
        except Exception:
            session_key = ""
        # Stage 1.70 compact diagnostics: the proof we need is that this exact
        # map/model profile was resolved in the current session. Logging every
        # individual spawn of the same model duplicated identical evidence.
        key = (session_key, int(rec.map_id or 0), int(rec.model_id or 0))
        if key in _LOGGED_OFFLINE_HITS:
            return
        _LOGGED_OFFLINE_HITS.add(key)
        if len(_LOGGED_OFFLINE_HITS) > 2048:
            _LOGGED_OFFLINE_HITS.clear()
            _LOGGED_OFFLINE_HITS.add(key)
        profile = OfflineKnowledge.enemy_profile(rec.model_id, rec.name, rec.map_id)
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        area = OfflineKnowledge.area_profile(rec.map_id)
        campaign = str(area.get("campaign", "") or "")
        fields = dict(
            map_id=int(rec.map_id or 0), agent_id=int(agent_id or 0),
            model_id=int(rec.model_id or 0), name=str(rec.name or profile.get("name") or "?"),
            known_skill_count=len(tuple(profile.get("known_skill_ids", ()) or ())),
            roles=",".join(str(x) for x in (profile.get("roles", ()) or ())),
            source=str(profile.get("source", "offline_knowledge")),
            campaign=campaign or "unknown",
            policy="keysojway_2_1_rc4_exact_map_model_first_contact_proof",
        )
        CombatDebug.log_event("OFFLINE_KNOWLEDGE_HIT", **fields)
        if campaign == "Nightfall":
            CombatDebug.log_event("NIGHTFALL_KNOWLEDGE_HIT", **fields)
        elif campaign == "Factions":
            CombatDebug.log_event("FACTIONS_KNOWLEDGE_HIT", **fields)
    except Exception:
        pass

def known_skills(agent_id: int) -> frozenset[int]:
    rec = observe(agent_id)
    merged = set(rec.observed_skills)
    try:
        profile = OfflineKnowledge.enemy_profile(rec.model_id, rec.name, rec.map_id)
        merged.update(int(v) for v in profile.get('known_skill_ids', ()) if int(v or 0) > 0)
    except Exception:
        pass
    if rec.model_id > 0:
        merged.update(_MODEL_MEMORY.get((rec.map_id, rec.model_id, rec.hard_mode), ()))
    return frozenset(merged)


def role_scores(agent_id: int) -> dict[str, int]:
    rec = observe(agent_id)
    monk, ritualist, mesmer, elementalist, necromancer, paragon = _profession_ids()
    profs = (rec.primary, rec.secondary)
    healer = 0
    caster = 0
    support = 0
    offense = 0
    if monk and monk in profs:
        healer += 95; support += 90; caster += 65
    if ritualist and ritualist in profs:
        healer += 70; support += 85; caster += 60
    if mesmer and mesmer in profs:
        caster += 90; offense += 85
    if elementalist and elementalist in profs:
        caster += 85; offense += 80
    if necromancer and necromancer in profs:
        caster += 75; offense += 70
    if paragon and paragon in profs:
        support += 45; offense += 35

    lname = str(rec.name or "").casefold()
    if any(token in lname for token in _HEALER_NAME_TOKENS):
        healer += 35; support += 25; caster += 20
    if any(token in lname for token in _SUPPORT_NAME_TOKENS):
        support += 30; caster += 15
    if any(token in lname for token in _CASTER_NAME_TOKENS):
        caster += 30; offense += 20

    skills = known_skills(agent_id)
    healer += 40 * len(skills.intersection(HEAL_SKILLS))
    support += 34 * len(skills.intersection(PROT_SKILLS))
    support += 55 * len(skills.intersection(REZ_SKILLS))
    offense += 28 * len(skills.intersection(DANGEROUS_OFFENSE_SKILLS))
    caster += min(90, len(skills) * 8)

    # Offline profiles provide immediate first-contact knowledge. Live behaviour
    # below can still outweigh or correct the static assumptions.
    try:
        enemy_profile = OfflineKnowledge.enemy_profile(rec.model_id, rec.name, rec.map_id)
        roles = set(enemy_profile.get("roles", ()))
        priority_bonus = int(enemy_profile.get("priority_bonus", 0) or 0)
        if "healer" in roles:
            healer += 85 + priority_bonus
        if "support" in roles:
            support += 70 + priority_bonus
        if "caster" in roles or "dangerous_caster" in roles:
            caster += 55
            offense += 45 + priority_bonus
        if "room_object" in roles:
            offense += 180 + priority_bonus

        for sid in skills:
            skill_profile = OfflineKnowledge.skill_profile(int(sid))
            categories = set(skill_profile.get("categories", ()))
            threat = int(skill_profile.get("threat", 0) or 0)
            if "heal" in categories:
                healer += max(20, threat // 2)
            if "protection" in categories or "party_protection" in categories:
                support += max(20, threat // 2)
            if "resurrection" in categories:
                support += max(70, threat)
            if "dangerous_offense" in categories or "shutdown" in categories:
                offense += max(20, threat // 2)
                caster += 15

        area = OfflineKnowledge.area_profile(rec.map_id)
        area_multiplier = float(area.get("threat_multiplier", 1.0) or 1.0)
        if area_multiplier > 1.0:
            healer = int(healer * area_multiplier)
            support = int(support * area_multiplier)
            caster = int(caster * min(1.15, area_multiplier))
            offense = int(offense * area_multiplier)
    except Exception:
        pass

    # Repeated live behaviour outweighs profession assumptions.
    healer += min(180, rec.heal_casts * 45)
    support += min(160, rec.prot_casts * 38 + rec.rez_casts * 70)
    offense += min(150, rec.dangerous_casts * 35)
    return {
        "healer": int(healer), "support": int(support),
        "caster": int(caster), "offense": int(offense),
    }


def threat_bonus(agent_id: int) -> int:
    scores = role_scores(agent_id)
    rec = observe(agent_id)
    bonus = max(scores["healer"], scores["support"], scores["offense"])
    if rec.hard_mode:
        bonus += 18
    if rec.level >= 28:
        bonus += min(25, rec.level - 27)
    return min(260, int(bonus * 0.55))


def mistrust_priority(agent_id: int) -> int:
    scores = role_scores(agent_id)
    # Mistrust is valuable on every real caster. Healers are not excluded:
    # preventing a heal/protection spell is often the fastest route to a kill.
    return int(scores["caster"] + scores["healer"] * 0.65 + scores["offense"] * 0.45)


def estimated_max_health(agent_id: int) -> int:
    """Conservative relative HP estimate for overkill planning.

    Guild Wars does not expose a universally reliable absolute max-HP value for
    every foe through the stable Python layer.  This estimate is therefore used
    only as a relative saturation hint; live health fraction remains the truth.
    """
    rec = observe(agent_id)
    level = max(1, int(rec.level or 1))
    base = 140 + level * 18
    if rec.hard_mode:
        base = int(base * 1.35)
    scores = role_scores(agent_id)
    if scores["healer"] > 120 or scores["support"] > 140:
        base = int(base * 1.08)
    return max(150, base)


def map_context() -> dict[str, object]:
    map_id, hard_mode = _safe_context()
    try:
        map_name = str(Map.GetMapName(map_id) or "")
    except Exception:
        map_name = ""
    return {"map_id": map_id, "map_name": map_name, "hard_mode": hard_mode}


def prune(live_agent_ids=()) -> None:
    """Bound heavy records without treating local scan loss as despawn.

    CombatSense passes a distance-filtered enemy set here.  Stage 17 incorrectly
    interpreted ordinary in/out-of-range movement as a spawn boundary, producing
    generation flapping.  Stage 18 therefore ignores that set for identity and
    only retires a heavy record after the long retention timeout.  The last static
    identity and generation are preserved so a later different occupant can still
    advance generation safely.
    """
    now = _now_ms()
    for aid, rec in list(_RECORDS.items()):
        stale = bool(now - int(rec.last_seen_ms or 0) > RETENTION_MS)
        if not stale:
            continue
        _AGENT_GENERATIONS[aid] = max(1, int(rec.generation or 1))
        _RETIRED_IDENTITIES[aid] = (
            int(rec.model_id or 0), int(rec.primary or 0), int(rec.secondary or 0), str(rec.name or "")
        )
        _RECORDS.pop(aid, None)
        _LOGGED_PROFILE_HASH.pop(aid, None)
        _LOGGED_PROFILE_TICK.pop(aid, None)


def reset() -> None:
    _RECORDS.clear()
    _MODEL_MEMORY.clear()
    _LOGGED_PROFILE_HASH.clear()
    _LOGGED_PROFILE_TICK.clear()
    _LOGGED_OFFLINE_HITS.clear()
    _PROCESSED_OUTCOMES.clear()
    _SEEN_CAST_SEQUENCES.clear()
    _AGENT_GENERATIONS.clear()
    _RETIRED_AGENT_IDS.clear()
    _RETIRED_IDENTITIES.clear()


def sync_event_outcomes(max_age_ms: int = 9000) -> None:
    """Learn outcome events once per session without replaying them as cast starts."""
    global _LAST_OUTCOME_SYNC_MS, _LAST_CONTEXT
    now = _now_ms()
    if now and _LAST_OUTCOME_SYNC_MS and now - _LAST_OUTCOME_SYNC_MS < 180:
        return
    _LAST_OUTCOME_SYNC_MS = now
    epoch = _ensure_session_epoch()
    context = _safe_context()
    _LAST_CONTEXT = context
    try:
        from Py4GWCoreLib.Builds.Skills import ReforgedSupport
        outcomes = ReforgedSupport.get_recent_cast_outcomes(max_age_ms=max_age_ms)
    except Exception:
        outcomes = ()
    for ts, aid, sid, outcome in outcomes:
        # EXP36 Stage 12 / E17: dedupe BEFORE observe.  Replayed outcome rows
        # therefore cannot increment EnemyKnowledge.total_casts.
        key = (int(epoch), int(ts), int(aid), int(sid), str(outcome))
        if key in _PROCESSED_OUTCOMES:
            continue
        _PROCESSED_OUTCOMES.add(key)
        rec = observe(int(aid), 0)
        if int(sid or 0) > 0:
            rec.observed_skills.add(int(sid))
            if rec.model_id > 0:
                _MODEL_MEMORY.setdefault((rec.map_id, rec.model_id, rec.hard_mode), set()).add(int(sid))
        if outcome == "finished": rec.finished_casts += 1
        elif outcome == "interrupted": rec.interrupted_casts += 1
        elif outcome == "stopped": rec.stopped_casts += 1
        # A native end event closes the polling fallback sequence explicitly.
        if int(rec.last_cast_skill_id or 0) == int(sid or 0):
            rec.last_cast_skill_id = 0
            rec.last_cast_seen_ms = int(now)
    if len(_PROCESSED_OUTCOMES) > 4096:
        _PROCESSED_OUTCOMES.clear()
    if len(_SEEN_CAST_SEQUENCES) > 4096:
        _SEEN_CAST_SEQUENCES.clear()
    purge_stale(now)


def purge_stale(now_ms: int = 0) -> None:
    now = int(now_ms or _now_ms())
    cutoff = now - int(RETENTION_MS)
    for aid, rec in list(_RECORDS.items()):
        if int(rec.last_seen_ms or 0) < cutoff:
            _AGENT_GENERATIONS[aid] = max(1, int(rec.generation or 1))
            _RETIRED_AGENT_IDS.add(int(aid))
            _RECORDS.pop(aid, None)
            _LOGGED_PROFILE_HASH.pop(aid, None)
            _LOGGED_PROFILE_TICK.pop(aid, None)


def profile_summary(agent_id: int) -> dict[str, object]:
    rec = observe(agent_id)
    scores = role_scores(agent_id)
    try:
        from Py4GWCoreLib.Builds.Skills import CombatSession
        session = CombatSession.refresh()
        session_epoch = int(session.epoch or 0)
        session_key = str(session.key)
    except Exception:
        session_epoch = 0
        session_key = ""
    return {
        "agent_id": int(agent_id or 0), "name": str(rec.name or "?"),
        "agent_generation": int(rec.generation or 1),
        "agent_identity_key": f"a{int(agent_id or 0)}:g{int(rec.generation or 1)}:m{int(rec.model_id or 0)}:p{int(rec.primary or 0)}",
        "session_epoch": int(session_epoch), "session_key": str(session_key),
        "model_id": int(rec.model_id), "level": int(rec.level),
        "hard_mode": bool(rec.hard_mode), "known_skill_count": len(known_skills(agent_id)),
        "casts": int(rec.total_casts), "finished": int(rec.finished_casts),
        "interrupted": int(rec.interrupted_casts), "stopped": int(rec.stopped_casts),
        **scores, "threat_bonus": int(threat_bonus(agent_id)),
    }


def log_profile_if_changed(agent_id: int) -> None:
    """Log only meaningful profile changes, heavily throttled.

    Raw counters can change every cast and previously produced thousands of
    lines.  The semantic signature below changes only when knowledge useful to
    target selection changes: new skills, role tier, or cast-outcome bucket.
    """
    try:
        summary = profile_summary(agent_id)
        aid = int(agent_id or 0)
        now = _now_ms()
        semantic = (
            int(summary.get("known_skill_count", 0)),
            int(summary.get("healer", 0)) // 40,
            int(summary.get("support", 0)) // 40,
            int(summary.get("caster", 0)) // 40,
            int(summary.get("offense", 0)) // 40,
            int(summary.get("finished", 0)) // 10,
            int(summary.get("interrupted", 0)) // 5,
            int(summary.get("stopped", 0)) // 5,
        )
        digest = hash(semantic)
        previous_digest = _LOGGED_PROFILE_HASH.get(aid)
        previous_tick = int(_LOGGED_PROFILE_TICK.get(aid, 0) or 0)
        if previous_digest == digest:
            return
        if previous_digest is not None and now - previous_tick < int(PROFILE_LOG_MIN_INTERVAL_MS):
            return
        _LOGGED_PROFILE_HASH[aid] = digest
        _LOGGED_PROFILE_TICK[aid] = int(now)
        # Make unnamed foes readable without relying on unstable name lookup.
        if str(summary.get("name", "?") or "?") == "?":
            summary["name"] = f"model:{int(summary.get('model_id', 0))}/prof:{int(observe(aid).primary)}"
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event("ENEMY_KNOWLEDGE_PROFILE", **summary)
    except Exception:
        pass
