"""Short-lived hard protection priority for the ST and its core spirits.

The owning CombatSense resolver calls this before ordinary packet selection.
When an active enemy is pressuring the Soul Twisting ritualist or its owned
Shelter/Union/Displacement/Earthbind package, the team briefly removes that
threat, then automatically returns to normal cluster/cleanup targeting.
"""
from __future__ import annotations

from Py4GWCoreLib import AgentArray, GLOBAL_CACHE, Range, Skill, Utils, Weapon
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.Map import Map
from Py4GWCoreLib.Party import Party

_SCAN_INTERVAL_MS = 180
_ST_CACHE_MS = 800
_PRESSURE_RADIUS = float(Range.Area.value)
_SPIRIT_LOSS_WINDOW_MS = 7000
_PRIORITY_HOLD_MS = 900
_HEALTH_DROP_EPSILON = 0.008

# Stage 2.16 Urgoz Splinter-pressure peel.  A Splinter-Weapon melee that is part
# of a multi-attacker dogpile on one ally/spirit is an immediate team kill
# target: removing the carrier deletes both its direct pressure and the repeated
# adjacent Splinter packets.
_URGOZ_MAP_ID = 266
_SPLINTER_WEAPON_ID = int(Skill.GetID("Splinter_Weapon") or 0)
_SPLINTER_PRESSURE_MIN_ATTACKERS = 2
_SPLINTER_PRIORITY_HOLD_MS = 1200
# Enemy weapon-spell effects are not reliably exposed by the effect cache.
# Track a completed Splinter cast's target instead; the real skill lasts long
# enough that a conservative 20 s lease is useful even without enemy buff data.
# Stage 2.19 / Astra P1-F: time inference is only suspicion.  Hard focus needs
# observed effect evidence; brief effect-cache holes get a tiny grace only.
_SPLINTER_SUSPECT_TTL_MS = 1200
_SPLINTER_EFFECT_GAP_GRACE_MS = 350
_SPLINTER_CAST_COMPLETE_GRACE_MS = 180
_RECOVERY_CORE_COVERAGE_HEADROOM = 180.0

_SOUL_TWISTING_ID = int(Skill.GetID("Soul_Twisting") or 0)
_CORE_SKILL_IDS = frozenset(
    int(Skill.GetID(name) or 0)
    for name in ("Shelter", "Union", "Displacement", "Earthbind")
    if int(Skill.GetID(name) or 0) > 0
)
_CORE_MODEL_VALUES = frozenset((2883, 2884, 2885, 2886))
try:
    from Py4GWCoreLib import SpiritModelID
    _CORE_MODEL_VALUES = frozenset(
        int(getattr(model, "value", model))
        for model in (
            SpiritModelID.SHELTER,
            SpiritModelID.UNION,
            SpiritModelID.DISPLACEMENT,
            SpiritModelID.EARTHBIND,
        )
    )
except Exception:
    pass

_LAST_SCAN_MS = 0
_LAST_ST_LOOKUP_MS = 0
_ST_AGENT_ID = 0
_LAST_SPIRITS: dict[int, tuple[tuple[float, float], float]] = {}
_LAST_ST_HP: float | None = None
_RECENT_LOSSES: list[int] = []
_PRIORITY_TARGET_ID = 0
_PRIORITY_UNTIL_MS = 0
_PRIORITY_BONUS = 0
_PRIORITY_REASON = ""
_WATCH_TARGET_ID = 0
_WATCH_UNTIL_MS = 0
_LAST_LOG_STATE: tuple[int, int, str] = (0, 0, "")
# Stage 2.18 V2: caster -> (recipient, frozen_expected_end_ms, first_seen_ms, source).
# Freeze the projected end on first observation so a missing cast-timer API cannot
# move the finish line forward every scheduler tick.
_SPLINTER_PENDING_CASTS: dict[int, tuple[int, int, int, str]] = {}
# recipient -> (effect_expiry_ms, last_effect_observed_ms)
_SPLINTER_TRACKED_CARRIERS: dict[int, tuple[int, int]] = {}
# recipient -> short suspicion expiry; never sufficient for Splinter hard focus.
_SPLINTER_SUSPECTED_CARRIERS: dict[int, int] = {}
_LAST_RECOVERY_COVERAGE_LOG: tuple = ()
_LAST_RECOVERY_COVERAGE_LOG_MS = 0


def _now_ms() -> int:
    try:
        import PySystem
        return int(PySystem.get_tick_count64() or 0)
    except Exception:
        import time
        return int(time.monotonic() * 1000.0)


def _log(event: str, **fields) -> None:
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(str(event), **fields)
    except Exception:
        pass


def _skillbar_ids(account) -> set[int]:
    try:
        skills = account.AgentData.Skillbar.Skills
    except Exception:
        return set()
    out: set[int] = set()
    for skill in skills or []:
        try:
            sid = int(getattr(skill, "Id", 0) or 0)
        except Exception:
            sid = 0
        if sid > 0:
            out.add(sid)
    return out


def _iter_party_accounts():
    try:
        from Py4GWCoreLib.HeroAI.utils import SameMapOrPartyAsAccount
        own_party_id = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
        seen: set[int] = set()
        for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
            if not getattr(account, "IsSlotActive", False) or getattr(account, "IsIsolated", False):
                continue
            if not SameMapOrPartyAsAccount(account):
                continue
            party_id = int(getattr(getattr(account, "AgentPartyData", None), "PartyID", 0) or 0)
            if own_party_id > 0 and party_id != own_party_id:
                continue
            agent_id = int(getattr(getattr(account, "AgentData", None), "AgentID", 0) or 0)
            if agent_id > 0 and agent_id not in seen:
                seen.add(agent_id)
                yield account
            for hero in GLOBAL_CACHE.ShMem.GetHeroesFromPlayers(agent_id) or []:
                if not getattr(hero, "IsSlotActive", False) or getattr(hero, "IsIsolated", False):
                    continue
                if not SameMapOrPartyAsAccount(hero):
                    continue
                hero_id = int(getattr(getattr(hero, "AgentData", None), "AgentID", 0) or 0)
                if hero_id > 0 and hero_id not in seen:
                    seen.add(hero_id)
                    yield hero
    except Exception:
        return


def _find_st_agent(now_ms: int) -> int:
    global _LAST_ST_LOOKUP_MS, _ST_AGENT_ID
    if _ST_AGENT_ID > 0 and now_ms - int(_LAST_ST_LOOKUP_MS) < _ST_CACHE_MS:
        try:
            if Agent.IsValid(_ST_AGENT_ID) and Agent.IsAlive(_ST_AGENT_ID):
                return int(_ST_AGENT_ID)
        except Exception:
            pass
    _LAST_ST_LOOKUP_MS = int(now_ms)
    _ST_AGENT_ID = 0
    if _SOUL_TWISTING_ID <= 0:
        return 0
    for account in _iter_party_accounts() or []:
        skills = _skillbar_ids(account)
        if _SOUL_TWISTING_ID not in skills:
            continue
        if len(skills.intersection(_CORE_SKILL_IDS)) < 2:
            continue
        try:
            aid = int(account.AgentData.AgentID or 0)
        except Exception:
            aid = 0
        if aid > 0:
            _ST_AGENT_ID = aid
            return aid
    return 0


def _xy(agent_id: int) -> tuple[float, float] | None:
    try:
        pos = Agent.GetXY(int(agent_id))
        return (float(pos[0]), float(pos[1])) if pos else None
    except Exception:
        return None


def _distance_xy(a, b) -> float:
    try:
        return float(Utils.Distance(a, b))
    except Exception:
        return 999999.0


def _owned_core_spirits(st_agent_id: int) -> dict[int, tuple[tuple[float, float], float]]:
    result: dict[int, tuple[tuple[float, float], float]] = {}
    for spirit_id in AgentArray.GetSpiritPetArray() or []:
        try:
            sid = int(spirit_id or 0)
            if sid <= 0 or not Agent.IsAlive(sid) or not Agent.IsSpawned(sid):
                continue
            if int(Agent.GetOwnerID(sid) or 0) != int(st_agent_id):
                continue
            model = int(Agent.GetPlayerNumber(sid) or 0)
            if model not in _CORE_MODEL_VALUES:
                continue
            pos = _xy(sid)
            if pos is None:
                continue
            result[sid] = (pos, float(Agent.GetHealth(sid) or 0.0))
        except Exception:
            continue
    return result


def _enemy_active(enemy) -> bool:
    return bool(getattr(enemy, "is_attacking", False) or getattr(enemy, "is_casting", False))


def _near_any(enemy, positions: list[tuple[float, float]], radius: float = _PRESSURE_RADIUS) -> bool:
    pos = getattr(enemy, "xy", None)
    if not pos:
        return False
    return any(_distance_xy(pos, center) <= float(radius) for center in positions)




def _party_agent_ids() -> set[int]:
    out: set[int] = set()
    try:
        for player in Party.GetPlayers() or []:
            login = int(getattr(player, "login_number", 0) or 0)
            aid = int(Party.Players.GetAgentIDByLoginNumber(login) or 0)
            if aid > 0:
                out.add(aid)
    except Exception:
        pass
    for getter_name in ("GetHeroes", "GetHenchmen"):
        try:
            getter = getattr(Party, getter_name)
            for member in getter() or []:
                aid = int(getattr(member, "agent_id", 0) or 0)
                if aid > 0:
                    out.add(aid)
        except Exception:
            continue
    return out


def _party_owned_spirit_ids(owner_ids: set[int]) -> set[int]:
    out: set[int] = set()
    if not owner_ids:
        return out
    try:
        for raw in AgentArray.GetSpiritPetArray() or []:
            sid = int(raw or 0)
            if sid <= 0 or not Agent.IsValid(sid) or not Agent.IsAlive(sid):
                continue
            owner = int(Agent.GetOwnerID(sid) or 0)
            if owner in owner_ids:
                out.add(sid)
    except Exception:
        pass
    return out


def _is_melee_enemy(agent_id: int) -> bool:
    """Prefer native melee classification; weapon telemetry is only fallback.

    Stage 2.17 used GetWeaponType exclusively.  Several Urgoz monsters expose a
    transient/unknown weapon type even while the native Agent classifier already
    knows they are melee, which prevented Splinter recipients from ever being
    accepted as carriers.
    """
    aid = int(agent_id or 0)
    if aid <= 0:
        return False
    try:
        if bool(Agent.IsMelee(aid)):
            return True
    except Exception:
        pass
    try:
        weapon_type = int(Agent.GetWeaponType(aid)[0] or 0)
        melee = {
            int(Weapon.Axe.value), int(Weapon.Hammer.value), int(Weapon.Daggers.value),
            int(Weapon.Scythe.value), int(Weapon.Sword.value),
        }
        return weapon_type in melee
    except Exception:
        return False


def _splinter_dogpile_fallback_recipient(caster_id: int, enemy_list) -> int:
    """Infer a high-confidence Splinter recipient only for the pressure case we care about.

    If enemy ally-target telemetry is unavailable during the Seer's weapon-spell
    cast, choose a melee ally in the Seer's spellcast range only when that melee
    is already part of a 2+ attacker dogpile on a party member/party spirit.  A
    wrong generic proximity guess would be worse than no carrier, so there is no
    fallback outside this high-pressure shape.
    """
    caster = int(caster_id or 0)
    cxy = _xy(caster)
    if caster <= 0 or cxy is None:
        return 0
    party_ids = _party_agent_ids()
    spirit_ids = _party_owned_spirit_ids(party_ids)
    protected = set(party_ids) | set(spirit_ids)
    if not protected:
        return 0

    attackers_by_target: dict[int, list[int]] = {}
    snapshots: dict[int, object] = {}
    for enemy in list(enemy_list or []):
        aid = int(getattr(enemy, "agent_id", 0) or 0)
        if aid <= 0 or aid == caster:
            continue
        snapshots[aid] = enemy
        try:
            if not bool(getattr(enemy, "is_attacking", False)) and not Agent.IsAttacking(aid):
                continue
            target_id = int(Agent.GetTarget(aid) or 0)
        except Exception:
            continue
        if target_id in protected:
            attackers_by_target.setdefault(int(target_id), []).append(aid)

    rows = []
    max_range = float(Range.Spellcast.value) + 120.0
    for victim_id, attackers in attackers_by_target.items():
        pressure = int(len(attackers))
        if pressure < int(_SPLINTER_PRESSURE_MIN_ATTACKERS):
            continue
        for aid in attackers:
            if not _is_melee_enemy(aid):
                continue
            pos = _xy(aid)
            if pos is None:
                continue
            distance = _distance_xy(cxy, pos)
            if distance > max_range:
                continue
            snap = snapshots.get(aid)
            threat = int(getattr(snap, "threat_score", 0) or 0) if snap is not None else 0
            cluster = max(
                int(getattr(snap, "adjacent_count", 1) or 1) if snap is not None else 1,
                int(getattr(snap, "predicted_adjacent_count", 1) or 1) if snap is not None else 1,
            )
            health = float(getattr(snap, "health", 1.0) or 1.0) if snap is not None else 1.0
            rows.append((pressure, threat, cluster, -distance, -health, -aid, aid))
    if not rows:
        return 0
    rows.sort(reverse=True)
    return int(rows[0][-1])


def _observed_splinter_effect_remaining_ms(agent_id: int) -> int:
    """Return direct effect evidence only; no timing-based inference."""
    aid = int(agent_id or 0)
    if aid <= 0 or _SPLINTER_WEAPON_ID <= 0:
        return 0
    try:
        remaining = int(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(aid, _SPLINTER_WEAPON_ID) or 0)
        if remaining > 0:
            return int(remaining)
    except Exception:
        pass
    try:
        effects = list(GLOBAL_CACHE.Effects.GetBuffs(aid) or []) + list(GLOBAL_CACHE.Effects.GetEffects(aid) or [])
        if any(int(getattr(effect, "skill_id", 0) or 0) == _SPLINTER_WEAPON_ID for effect in effects):
            return 1
    except Exception:
        pass
    return 0


def _observe_splinter_cast_targets(enemy_list, now_ms: int) -> None:
    """Stage 2.19: separate observed cast/recipient, suspicion and confirmed effect.

    A cast disappearing near its projected end is *not* proof of completion.  It
    may have been interrupted between scheduler polls.  Only a directly observed
    Splinter effect can create a hard-focus carrier lease.  A direct recipient
    whose cast plausibly completed gets only a short suspicion marker; normal ST
    pressure handling remains free to peel that melee, but Splinter hard focus
    never treats the suspicion as an active weapon spell.
    """
    if _SPLINTER_WEAPON_ID <= 0:
        return
    try:
        if int(Map.GetMapID() or 0) != int(_URGOZ_MAP_ID):
            _SPLINTER_PENDING_CASTS.clear()
            _SPLINTER_TRACKED_CARRIERS.clear()
            _SPLINTER_SUSPECTED_CARRIERS.clear()
            return
    except Exception:
        return

    now = int(now_ms or _now_ms())
    enemy_list = list(enemy_list or [])
    enemy_ids = {int(getattr(e, "agent_id", 0) or 0) for e in enemy_list}

    # Refresh/expire confirmed carriers from effect evidence itself.
    for carrier, row in list(_SPLINTER_TRACKED_CARRIERS.items()):
        try:
            expiry, last_seen = int(row[0]), int(row[1])
        except Exception:
            expiry, last_seen = 0, 0
        alive = False
        try:
            alive = bool(Agent.IsValid(int(carrier)) and Agent.IsAlive(int(carrier)))
        except Exception:
            alive = False
        if not alive or expiry <= now or now - last_seen > int(_SPLINTER_EFFECT_GAP_GRACE_MS):
            _SPLINTER_TRACKED_CARRIERS.pop(int(carrier), None)
    for carrier, expiry in list(_SPLINTER_SUSPECTED_CARRIERS.items()):
        if int(expiry) <= now:
            _SPLINTER_SUSPECTED_CARRIERS.pop(int(carrier), None)

    live_splinter_casters: set[int] = set()
    for enemy in enemy_list:
        caster = int(getattr(enemy, "agent_id", 0) or 0)
        if caster <= 0:
            continue
        try:
            if not Agent.IsCasting(caster) or int(Agent.GetCastingSkillID(caster) or 0) != int(_SPLINTER_WEAPON_ID):
                continue
        except Exception:
            continue
        live_splinter_casters.add(caster)
        old = _SPLINTER_PENDING_CASTS.get(caster)
        first_seen = int(old[2]) if old else now
        source = str(old[3]) if old else ""
        recipient = int(old[0]) if old else 0

        try:
            direct = int(Agent.GetTarget(caster) or 0)
        except Exception:
            direct = 0
        try:
            direct_valid = bool(direct > 0 and direct in enemy_ids and Agent.IsValid(direct) and Agent.IsAlive(direct) and _is_melee_enemy(direct))
        except Exception:
            direct_valid = False
        if direct_valid:
            recipient = int(direct)
            source = "direct_cast_target"
        elif recipient <= 0:
            fallback = int(_splinter_dogpile_fallback_recipient(caster, enemy_list) or 0)
            if fallback > 0:
                recipient = int(fallback)
                source = "dogpile_fallback_suspected_recipient"

        if old:
            expected_end = int(old[1])
        else:
            remaining = None
            try:
                from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import get_enemy_cast_remaining_ms
                remaining = get_enemy_cast_remaining_ms(caster, int(_SPLINTER_WEAPON_ID))
            except Exception:
                remaining = None
            if remaining is None or int(remaining) <= 0:
                try:
                    remaining = int(float(GLOBAL_CACHE.Skill.Data.GetActivation(int(_SPLINTER_WEAPON_ID)) or 1.0) * 1000.0)
                except Exception:
                    remaining = 1000
            expected_end = int(now + max(1, int(remaining)))

        previous_recipient = int(old[0]) if old else 0
        _SPLINTER_PENDING_CASTS[caster] = (int(recipient), int(expected_end), int(first_seen), str(source or "unresolved"))
        if (old is None) or (recipient > 0 and recipient != previous_recipient):
            _log(
                "URGOZ_SPLINTER_CAST_TARGET_TRACKED" if recipient > 0 else "URGOZ_SPLINTER_CAST_TARGET_UNRESOLVED",
                caster_id=int(caster), carrier_id=int(recipient), source=str(source or "unresolved"),
                expected_end_ms=int(expected_end), first_seen_ms=int(first_seen),
                evidence="cast_observed_recipient_observed" if source == "direct_cast_target" else "cast_observed_recipient_inferred",
                policy="stage2_19_p1f_observation_is_not_completion",
            )

        # If the weapon-spell effect is already observable, that is enough proof
        # regardless of whether the cast-completion edge was sampled.
        if recipient > 0:
            effect_remaining = int(_observed_splinter_effect_remaining_ms(recipient))
            if effect_remaining > 0:
                expiry = int(now + (effect_remaining if effect_remaining > 1 else _SPLINTER_EFFECT_GAP_GRACE_MS))
                _SPLINTER_TRACKED_CARRIERS[int(recipient)] = (expiry, int(now))
                _SPLINTER_SUSPECTED_CARRIERS.pop(int(recipient), None)

    for caster, pending in list(_SPLINTER_PENDING_CASTS.items()):
        recipient, expected_end, first_seen, source = pending
        if int(caster) in live_splinter_casters:
            continue
        _SPLINTER_PENDING_CASTS.pop(int(caster), None)
        stopped_early = bool(now + int(_SPLINTER_CAST_COMPLETE_GRACE_MS) < int(expected_end))
        if stopped_early:
            _log(
                "URGOZ_SPLINTER_CAST_TRACK_ABORTED", caster_id=int(caster), carrier_id=int(recipient),
                source=str(source), stopped_early_ms=max(0, int(expected_end) - now),
                policy="stage2_19_p1f_early_stop_revokes_pending",
            )
            continue

        try:
            valid = bool(int(recipient) in enemy_ids and Agent.IsValid(int(recipient)) and Agent.IsAlive(int(recipient)) and _is_melee_enemy(int(recipient)))
        except Exception:
            valid = False
        if not valid:
            _log(
                "URGOZ_SPLINTER_CARRIER_UNRESOLVED", caster_id=int(caster), carrier_id=int(recipient),
                source=str(source or "unresolved"), observed_cast_ms=max(0, now - int(first_seen)),
                policy="stage2_19_p1f_no_target_invention_after_cast_disappears",
            )
            continue

        effect_remaining = int(_observed_splinter_effect_remaining_ms(int(recipient)))
        if effect_remaining > 0:
            expiry = int(now + (effect_remaining if effect_remaining > 1 else _SPLINTER_EFFECT_GAP_GRACE_MS))
            _SPLINTER_TRACKED_CARRIERS[int(recipient)] = (expiry, int(now))
            _SPLINTER_SUSPECTED_CARRIERS.pop(int(recipient), None)
            _log(
                "URGOZ_SPLINTER_CARRIER_CONFIRMED", caster_id=int(caster), carrier_id=int(recipient),
                source=str(source), effect_remaining_ms=int(effect_remaining), observed_cast_ms=max(0, now - int(first_seen)),
                evidence="effect_observed", policy="stage2_19_p1f_hard_focus_requires_effect_evidence",
            )
            continue

        # A plausible completion is useful diagnostic evidence, but is not a
        # confirmed carrier.  Direct target evidence gets a short suspicion lease;
        # inferred targets get an even shorter one.  Neither reaches hard focus.
        suspect_ttl = int(_SPLINTER_SUSPECT_TTL_MS if str(source) == "direct_cast_target" else _SPLINTER_SUSPECT_TTL_MS // 2)
        _SPLINTER_SUSPECTED_CARRIERS[int(recipient)] = int(now + suspect_ttl)
        _log(
            "URGOZ_SPLINTER_CARRIER_SUSPECTED", caster_id=int(caster), carrier_id=int(recipient),
            source=str(source), suspect_ttl_ms=int(suspect_ttl), observed_cast_ms=max(0, now - int(first_seen)),
            evidence="cast_disappeared_near_expected_end_without_effect_proof",
            policy="stage2_19_p1f_suspicion_never_promoted_to_confirmed_by_time_alone",
        )


def _splinter_remaining_ms(agent_id: int) -> int:
    aid = int(agent_id or 0)
    if aid <= 0 or _SPLINTER_WEAPON_ID <= 0:
        return 0
    now = _now_ms()
    observed = int(_observed_splinter_effect_remaining_ms(aid))
    if observed > 0:
        expiry = int(now + (observed if observed > 1 else _SPLINTER_EFFECT_GAP_GRACE_MS))
        _SPLINTER_TRACKED_CARRIERS[aid] = (expiry, int(now))
        _SPLINTER_SUSPECTED_CARRIERS.pop(aid, None)
        return int(observed)
    row = _SPLINTER_TRACKED_CARRIERS.get(aid)
    if row is not None:
        try:
            expiry, last_seen = int(row[0]), int(row[1])
        except Exception:
            expiry, last_seen = 0, 0
        if expiry > now and now - last_seen <= int(_SPLINTER_EFFECT_GAP_GRACE_MS):
            return max(1, min(int(expiry - now), int(_SPLINTER_EFFECT_GAP_GRACE_MS)))
    _SPLINTER_TRACKED_CARRIERS.pop(aid, None)
    return 0


def _urgoz_splinter_pressure_target(enemy_list) -> tuple[int, int, int, bool, int]:
    """Return (carrier, attackers_on_victim, victim, victim_is_spirit, remaining_ms).

    This is deliberately exact: Urgoz only, active melee carrier with the actual
    Splinter Weapon effect, and at least two enemies currently targeting the same
    party member or party-owned spirit.  If target telemetry is unavailable, the
    rule simply does not fire rather than guessing from proximity.
    """
    try:
        if int(Map.GetMapID() or 0) != int(_URGOZ_MAP_ID):
            return 0, 0, 0, False, 0
    except Exception:
        return 0, 0, 0, False, 0

    party_ids = _party_agent_ids()
    spirit_ids = _party_owned_spirit_ids(party_ids)
    protected_ids = set(party_ids) | set(spirit_ids)
    if not protected_ids:
        return 0, 0, 0, False, 0

    attackers_by_target: dict[int, list[object]] = {}
    for enemy in list(enemy_list or []):
        aid = int(getattr(enemy, "agent_id", 0) or 0)
        if aid <= 0 or not bool(getattr(enemy, "is_attacking", False)):
            continue
        try:
            target_id = int(Agent.GetTarget(aid) or 0)
        except Exception:
            target_id = 0
        if target_id <= 0 or target_id not in protected_ids:
            continue
        attackers_by_target.setdefault(target_id, []).append(enemy)

    rows = []
    for victim_id, attackers in attackers_by_target.items():
        pressure = len(attackers)
        if pressure < int(_SPLINTER_PRESSURE_MIN_ATTACKERS):
            continue
        victim_is_spirit = victim_id in spirit_ids
        for enemy in attackers:
            aid = int(getattr(enemy, "agent_id", 0) or 0)
            if aid <= 0 or not _is_melee_enemy(aid):
                continue
            remaining = _splinter_remaining_ms(aid)
            if remaining <= 0:
                continue
            health = max(0.0, float(getattr(enemy, "health", 1.0) or 1.0))
            cluster = max(int(getattr(enemy, "adjacent_count", 1) or 1), int(getattr(enemy, "predicted_adjacent_count", 1) or 1))
            threat = int(getattr(enemy, "threat_score", 0) or 0)
            # Protecting a spirit gets a slight edge, then remove the carrier in
            # the heaviest dogpile.  Within equal pressure, finish the easier kill.
            rows.append((int(victim_is_spirit), int(pressure), -float(health), int(threat), int(cluster), int(remaining), -aid, aid, victim_id))
    if not rows:
        return 0, 0, 0, False, 0
    rows.sort(reverse=True)
    _spirit, pressure, _neg_hp, _threat, _cluster, remaining, _neg_aid, aid, victim_id = rows[0]
    return int(aid), int(pressure), int(victim_id), bool(victim_id in spirit_ids), int(remaining)



def _recovery_probe_xy(anchor_xy: tuple[float, float] | None) -> tuple[float, float] | None:
    if anchor_xy is not None:
        try:
            return float(anchor_xy[0]), float(anchor_xy[1])
        except Exception:
            pass
    try:
        leader = int(Party.GetPartyLeaderID() or 0)
        if leader > 0 and Agent.IsValid(leader) and Agent.IsAlive(leader):
            pos = Agent.GetXY(leader)
            if pos:
                return float(pos[0]), float(pos[1])
    except Exception:
        pass
    living = []
    for aid in _party_agent_ids():
        try:
            if Agent.IsValid(aid) and Agent.IsAlive(aid):
                pos = Agent.GetXY(aid)
                if pos:
                    living.append((float(pos[0]), float(pos[1])))
        except Exception:
            continue
    if not living:
        return None
    return (sum(x for x, _ in living) / len(living), sum(y for _, y in living) / len(living))


def recovery_core_protection_details(anchor_xy: tuple[float, float] | None = None) -> dict[str, object]:
    """Fresh recovery view: present spirits are not equivalent to local coverage."""
    global _LAST_RECOVERY_COVERAGE_LOG, _LAST_RECOVERY_COVERAGE_LOG_MS
    now = _now_ms()
    st_id = _find_st_agent(now)
    if st_id <= 0:
        return {"known": False, "ready": True, "required": 0, "present": 0, "covered": 0, "missing": (), "out_of_range": (), "anchor": None, "observed_at_ms": int(now)}
    try:
        from Py4GWCoreLib import SpiritModelID
        skill_to_model = {
            int(Skill.GetID("Shelter") or 0): int(getattr(SpiritModelID.SHELTER, "value", SpiritModelID.SHELTER)),
            int(Skill.GetID("Union") or 0): int(getattr(SpiritModelID.UNION, "value", SpiritModelID.UNION)),
            int(Skill.GetID("Displacement") or 0): int(getattr(SpiritModelID.DISPLACEMENT, "value", SpiritModelID.DISPLACEMENT)),
        }
    except Exception:
        return {"known": False, "ready": True, "required": 0, "present": 0, "covered": 0, "missing": (), "out_of_range": (), "anchor": None, "observed_at_ms": int(now)}

    equipped: set[int] = set()
    try:
        for account in _iter_party_accounts() or []:
            aid = int(getattr(getattr(account, "AgentData", None), "AgentID", 0) or 0)
            if aid == int(st_id):
                equipped = _skillbar_ids(account)
                break
    except Exception:
        equipped = set()
    required_models = {int(model) for sid, model in skill_to_model.items() if sid > 0 and sid in equipped and int(model) > 0}
    if not required_models:
        return {"known": False, "ready": True, "required": 0, "present": 0, "covered": 0, "missing": (), "out_of_range": (), "anchor": None, "observed_at_ms": int(now)}

    probe = _recovery_probe_xy(anchor_xy)
    if probe is None:
        return {"known": False, "ready": True, "required": len(required_models), "present": 0, "covered": 0, "missing": (), "out_of_range": (), "anchor": None, "observed_at_ms": int(now)}

    # Stage 2.24: use the same ownership tolerance as the owning ST controller.
    # Reforged/Py4GW can briefly expose freshly-created spirits with owner=0;
    # treating those as absent made TeamRecovery report 0/3 while the ST itself
    # was already protecting the party. Prefer exact ownership, then ownerless,
    # then a nearby same-model compatibility fallback.
    owned_by_model: dict[int, list[int]] = {int(m): [] for m in required_models}
    ownerless_by_model: dict[int, list[int]] = {int(m): [] for m in required_models}
    nearby_by_model: dict[int, list[int]] = {int(m): [] for m in required_models}
    st_xy = _xy(int(st_id))
    try:
        for raw in AgentArray.GetSpiritPetArray() or []:
            sid = int(raw or 0)
            if sid <= 0 or not Agent.IsValid(sid) or not Agent.IsAlive(sid) or not Agent.IsSpawned(sid):
                continue
            model = int(Agent.GetPlayerNumber(sid) or 0)
            if model not in required_models:
                continue
            owner_id = int(Agent.GetOwnerID(sid) or 0)
            if owner_id == int(st_id):
                owned_by_model.setdefault(model, []).append(sid)
            elif owner_id == 0:
                ownerless_by_model.setdefault(model, []).append(sid)
            else:
                # Last-resort parity with Soul Twisting.py's nearby-model
                # fallback, but keep it local to the ST to avoid remote spirits.
                pos = _xy(sid)
                if st_xy is not None and pos is not None and _distance_xy(pos, st_xy) <= float(Range.Spirit.value):
                    nearby_by_model.setdefault(model, []).append(sid)
    except Exception:
        return {"known": False, "ready": True, "required": len(required_models), "present": 0, "covered": 0, "missing": (), "out_of_range": (), "anchor": probe, "observed_at_ms": int(now)}

    by_model: dict[int, list[int]] = {}
    ownership_fallback_models: list[int] = []
    nearby_fallback_models: list[int] = []
    for model in required_models:
        model = int(model)
        if owned_by_model.get(model):
            by_model[model] = list(owned_by_model[model])
        elif ownerless_by_model.get(model):
            by_model[model] = list(ownerless_by_model[model])
            ownership_fallback_models.append(model)
        else:
            by_model[model] = list(nearby_by_model.get(model, ()))
            if by_model[model]:
                nearby_fallback_models.append(model)

    coverage_limit = max(0.0, float(Range.Spirit.value) - float(_RECOVERY_CORE_COVERAGE_HEADROOM))
    present_models = {model for model, spirits in by_model.items() if spirits}
    covered_models: set[int] = set()
    for model, spirits in by_model.items():
        for sid in spirits:
            pos = _xy(int(sid))
            if pos is not None and _distance_xy(pos, probe) <= coverage_limit:
                covered_models.add(int(model))
                break
    missing = tuple(sorted(required_models.difference(present_models)))
    out_of_range = tuple(sorted(present_models.difference(covered_models)))
    ready = bool(required_models.issubset(covered_models))
    signature = (bool(ready), len(required_models), len(present_models), len(covered_models), missing, out_of_range, round(float(probe[0]), -1), round(float(probe[1]), -1))
    if signature != _LAST_RECOVERY_COVERAGE_LOG or now - int(_LAST_RECOVERY_COVERAGE_LOG_MS or 0) >= 3000:
        _LAST_RECOVERY_COVERAGE_LOG = signature
        _LAST_RECOVERY_COVERAGE_LOG_MS = int(now)
        _log(
            "TEAM_RECOVERY_CORE_COVERAGE", ready=bool(ready), required=int(len(required_models)),
            present=int(len(present_models)), covered=int(len(covered_models)),
            missing_models=",".join(str(v) for v in missing), out_of_range_models=",".join(str(v) for v in out_of_range),
            anchor=f"{float(probe[0]):.1f},{float(probe[1]):.1f}", coverage_limit=round(float(coverage_limit), 1),
            ownerless_fallback_models=",".join(str(v) for v in sorted(ownership_fallback_models)),
            nearby_fallback_models=",".join(str(v) for v in sorted(nearby_fallback_models)),
            observed_at_ms=int(now), policy="stage2_24_owner_tolerant_presence_plus_range_rejoin_readiness",
        )
    return {
        "known": True, "ready": bool(ready), "required": len(required_models),
        "present": len(present_models), "covered": len(covered_models),
        "missing": missing, "out_of_range": out_of_range, "anchor": probe,
        "ownerless_fallback_models": tuple(sorted(ownership_fallback_models)),
        "nearby_fallback_models": tuple(sorted(nearby_fallback_models)),
        "observed_at_ms": int(now),
    }


def recovery_core_protection_status(anchor_xy: tuple[float, float] | None = None) -> tuple[bool, bool, int, int, tuple[int, ...]]:
    """Compatibility tuple: active now means locally-covered core models."""
    details = recovery_core_protection_details(anchor_xy=anchor_xy)
    unresolved = tuple(sorted(set(tuple(details.get("missing", ()) or ())) | set(tuple(details.get("out_of_range", ()) or ()))))
    return (
        bool(details.get("known", False)), bool(details.get("ready", True)),
        int(details.get("required", 0) or 0), int(details.get("covered", 0) or 0), unresolved,
    )

def get_priority(enemies) -> tuple[int, int, str]:
    """Return ``(target_id, score_bonus, reason)`` for immediate ST protection.

    A positive target is consumed by CombatSense as a short-lived hard focus.
    The score bonus is retained for diagnostics/backward compatibility only.
    """
    global _LAST_SCAN_MS, _LAST_SPIRITS, _LAST_ST_HP, _RECENT_LOSSES
    global _PRIORITY_TARGET_ID, _PRIORITY_UNTIL_MS, _PRIORITY_BONUS, _PRIORITY_REASON, _WATCH_TARGET_ID, _WATCH_UNTIL_MS, _LAST_LOG_STATE

    now = _now_ms()
    enemy_ids = {int(getattr(e, "agent_id", 0) or 0) for e in (enemies or [])}
    if now - int(_LAST_SCAN_MS) < _SCAN_INTERVAL_MS:
        if (
            int(_PRIORITY_TARGET_ID) > 0
            and int(_PRIORITY_TARGET_ID) in enemy_ids
            and now < int(_PRIORITY_UNTIL_MS)
        ):
            return int(_PRIORITY_TARGET_ID), int(_PRIORITY_BONUS), str(_PRIORITY_REASON)
        return 0, 0, ""
    _LAST_SCAN_MS = int(now)

    st_id = _find_st_agent(now)
    if st_id <= 0:
        _LAST_SPIRITS = {}
        _LAST_ST_HP = None
        _RECENT_LOSSES.clear()
        _PRIORITY_TARGET_ID = 0
        _PRIORITY_UNTIL_MS = 0
        _PRIORITY_BONUS = 0
        _PRIORITY_REASON = ""
        _WATCH_TARGET_ID = 0
        _WATCH_UNTIL_MS = 0
        _SPLINTER_PENDING_CASTS.clear()
        _SPLINTER_TRACKED_CARRIERS.clear()
        _SPLINTER_SUSPECTED_CARRIERS.clear()
        return 0, 0, ""

    st_pos = _xy(st_id)
    try:
        st_hp = float(Agent.GetHealth(st_id) or 0.0)
    except Exception:
        st_hp = 0.0
    current_spirits = _owned_core_spirits(st_id)
    _observe_splinter_cast_targets(enemies, now)
    spirit_positions = [pos for pos, _hp in current_spirits.values()]
    positions = ([st_pos] if st_pos else []) + spirit_positions

    enemy_list = list(enemies or [])
    active_pressure = [e for e in enemy_list if _enemy_active(e) and _near_any(e, positions)]
    all_close = [e for e in enemy_list if _near_any(e, positions)]

    # Stage 2.16: hard-focus a Splinter-Weapon melee that is participating in a
    # real dogpile on one ally/party spirit.  This uses the existing ST-defense
    # special-priority channel so every player/hero controller receives the same
    # target through TeamCombatFocus.
    splinter_target, splinter_attackers, splinter_victim, victim_is_spirit, splinter_remaining = _urgoz_splinter_pressure_target(enemy_list)
    if splinter_target > 0:
        _PRIORITY_TARGET_ID = int(splinter_target)
        _PRIORITY_UNTIL_MS = int(now) + int(_SPLINTER_PRIORITY_HOLD_MS)
        _PRIORITY_BONUS = 8000
        _PRIORITY_REASON = "urgoz_splinter_melee_pressure"
        _WATCH_TARGET_ID = int(splinter_target)
        _WATCH_UNTIL_MS = int(now) + int(_SPLINTER_PRIORITY_HOLD_MS)
        state = (int(splinter_target), 4, str(_PRIORITY_REASON))
        if state != _LAST_LOG_STATE:
            _LAST_LOG_STATE = state
            _log(
                "URGOZ_SPLINTER_MELEE_HARD_FOCUS",
                target_id=int(splinter_target),
                victim_id=int(splinter_victim),
                victim_is_spirit=bool(victim_is_spirit),
                attackers_on_victim=int(splinter_attackers),
                splinter_remaining_ms=int(splinter_remaining),
                policy="hard_focus_splinter_carrier_when_multi_enemy_dogpile_hits_ally_or_party_spirit",
            )
        return int(splinter_target), int(_PRIORITY_BONUS), str(_PRIORITY_REASON)

    damaged_positions: list[tuple[float, float]] = []
    st_took_damage = bool(
        st_pos
        and _LAST_ST_HP is not None
        and st_hp > 0.0
        and float(_LAST_ST_HP) - float(st_hp) >= _HEALTH_DROP_EPSILON
    )
    if st_took_damage and st_pos:
        damaged_positions.append(st_pos)
    for spirit_id, (spirit_pos, spirit_hp) in current_spirits.items():
        previous = _LAST_SPIRITS.get(int(spirit_id))
        if previous is None:
            continue
        previous_hp = float(previous[1])
        if previous_hp - float(spirit_hp) >= _HEALTH_DROP_EPSILON:
            damaged_positions.append(spirit_pos)

    # Count a disappeared core spirit as a combat loss only when it was already
    # damaged or active enemies remained at its last position.  This filters
    # ordinary expiry/map cleanup while still catching rapid focus deaths.
    for spirit_id, (last_pos, last_hp) in list(_LAST_SPIRITS.items()):
        if spirit_id in current_spirits:
            continue
        enemies_at_old_spirit = [e for e in enemy_list if _near_any(e, [last_pos])]
        active_at_old_spirit = [e for e in enemies_at_old_spirit if _enemy_active(e)]
        if float(last_hp) < 0.80 or len(active_at_old_spirit) >= 2:
            _RECENT_LOSSES.append(int(now))
    _LAST_SPIRITS = current_spirits
    _LAST_ST_HP = float(st_hp) if st_hp > 0.0 else _LAST_ST_HP
    _RECENT_LOSSES[:] = [tick for tick in _RECENT_LOSSES if now - int(tick) <= _SPIRIT_LOSS_WINDOW_MS]

    st_active = [e for e in active_pressure if st_pos and _near_any(e, [st_pos])]
    spirit_active = [e for e in active_pressure if spirit_positions and _near_any(e, spirit_positions)]
    recent_losses = len(_RECENT_LOSSES)

    severity = 0
    reason = ""
    damage_pressure = [
        e for e in enemy_list
        if damaged_positions and _enemy_active(e) and _near_any(e, damaged_positions)
    ]
    if recent_losses >= 2 and len(all_close) >= 1:
        severity = 3
        reason = "repeated_spirit_loss"
    elif damage_pressure:
        severity = 3
        reason = "st_or_core_spirit_took_damage"
    elif len(st_active) >= 2 or len(spirit_active) >= 2:
        severity = 2
        reason = "multiple_st_spirit_attackers"
    elif len(st_active) >= 1 or len(spirit_active) >= 1:
        severity = 1
        reason = "direct_st_spirit_pressure"

    pressure_candidates = damage_pressure or active_pressure or (all_close if recent_losses >= 2 else [])
    if severity <= 0 or not pressure_candidates:
        if (
            int(_PRIORITY_TARGET_ID) > 0
            and int(_PRIORITY_TARGET_ID) in enemy_ids
            and now < int(_PRIORITY_UNTIL_MS)
        ):
            return int(_PRIORITY_TARGET_ID), int(_PRIORITY_BONUS), str(_PRIORITY_REASON)
        _PRIORITY_TARGET_ID = 0
        _PRIORITY_BONUS = 0
        _PRIORITY_REASON = ""
        _WATCH_TARGET_ID = 0
        _WATCH_UNTIL_MS = 0
        return 0, 0, ""

    def sort_key(enemy):
        cluster = max(int(getattr(enemy, "adjacent_count", 1)), int(getattr(enemy, "predicted_adjacent_count", 1)))
        threat = int(getattr(enemy, "threat_score", 0))
        health = float(getattr(enemy, "health", 1.0))
        active = 1 if _enemy_active(enemy) else 0
        pos = getattr(enemy, "xy", None)
        protected_distance = min(
            (_distance_xy(pos, protected_pos) for protected_pos in positions),
            default=999999.0,
        ) if pos else 999999.0
        damaged_distance = min(
            (_distance_xy(pos, damaged_pos) for damaged_pos in damaged_positions),
            default=999999.0,
        ) if pos and damaged_positions else protected_distance
        return (damaged_distance, protected_distance, -active, -threat, -cluster, health, int(getattr(enemy, "agent_id", 0)))

    pressure_candidates.sort(key=sort_key)
    chosen = int(getattr(pressure_candidates[0], "agent_id", 0) or 0)
    if chosen <= 0:
        return 0, 0, ""

    # v30: one merely active foe inside the ST/core-spirit envelope is a watch
    # condition, not a reason to tear the whole damage packet off its target.
    # Exact hostile target ownership is not exposed reliably by the current
    # Reforged Python API, so severity 1 is intentionally handled by the normal
    # PI/danger-interrupt layer unless it escalates to multiple pressurers or
    # actual ST/core-spirit damage. A just-fired severe priority may finish its
    # short hold, but severity 1 never creates or refreshes a hard team focus.
    if severity == 1:
        _WATCH_TARGET_ID = int(chosen)
        _WATCH_UNTIL_MS = int(now) + _PRIORITY_HOLD_MS
        state = (int(chosen), 1, str(reason))
        if state != _LAST_LOG_STATE:
            _LAST_LOG_STATE = state
            _log(
                "ST_DEFENSE_WATCH_ONLY",
                target_id=int(chosen),
                severity=1,
                reason=str(reason),
                st_attackers=len(st_active),
                spirit_attackers=len(spirit_active),
                policy="single_nearby_active_enemy_does_not_break_team_packet",
            )
        if (
            int(_PRIORITY_TARGET_ID) > 0
            and int(_PRIORITY_TARGET_ID) in enemy_ids
            and now < int(_PRIORITY_UNTIL_MS)
        ):
            return int(_PRIORITY_TARGET_ID), int(_PRIORITY_BONUS), str(_PRIORITY_REASON)
        _PRIORITY_TARGET_ID = 0
        _PRIORITY_UNTIL_MS = 0
        _PRIORITY_BONUS = 0
        _PRIORITY_REASON = ""
        return 0, 0, ""

    _PRIORITY_TARGET_ID = int(chosen)
    _PRIORITY_UNTIL_MS = int(now) + _PRIORITY_HOLD_MS
    _WATCH_TARGET_ID = int(chosen)
    _WATCH_UNTIL_MS = int(now) + _PRIORITY_HOLD_MS
    bonus = 6000 if severity >= 3 else (4500 if severity >= 2 else 3000)
    _PRIORITY_BONUS = int(bonus)
    _PRIORITY_REASON = str(reason)
    state = (int(chosen), int(severity), str(reason))
    if state != _LAST_LOG_STATE:
        _LAST_LOG_STATE = state
        _log(
            "ST_DEFENSE_PRIORITY_ACTIVE",
            target_id=int(chosen),
            severity=int(severity),
            reason=str(reason),
            st_attackers=len(st_active),
            spirit_attackers=len(spirit_active),
            recent_spirit_losses=int(recent_losses),
            st_took_damage=bool(st_took_damage),
            damaged_core_positions=int(len(damaged_positions)),
        )
    return int(chosen), int(bonus), str(reason)


def is_priority_target(agent_id: int) -> bool:
    """Return whether ``agent_id`` is the currently held ST-defense focus."""
    aid = int(agent_id or 0)
    return bool(
        aid > 0
        and aid == int(_PRIORITY_TARGET_ID or 0)
        and _now_ms() < int(_PRIORITY_UNTIL_MS or 0)
    )


def protection_pressure_rank(agent_id: int) -> int:
    """Return skill-local ST/core-spirit pressure priority without changing team focus.

    2 = current hard ST-defense focus, 1 = single active watch target near the
    ST/core-spirit envelope, 0 = no current protection pressure.  The watch
    state exists specifically so anti-martial skills can protect the backline
    without forcing the entire Mesmer team off its damage packet.
    """
    aid = int(agent_id or 0)
    now = _now_ms()
    if aid <= 0:
        return 0
    if aid == int(_PRIORITY_TARGET_ID or 0) and now < int(_PRIORITY_UNTIL_MS or 0):
        return 2
    if aid == int(_WATCH_TARGET_ID or 0) and now < int(_WATCH_UNTIL_MS or 0):
        return 1
    return 0


def reset() -> None:
    global _LAST_SCAN_MS, _LAST_ST_LOOKUP_MS, _ST_AGENT_ID, _LAST_SPIRITS, _LAST_ST_HP
    global _PRIORITY_TARGET_ID, _PRIORITY_UNTIL_MS, _PRIORITY_BONUS, _PRIORITY_REASON, _WATCH_TARGET_ID, _WATCH_UNTIL_MS, _LAST_LOG_STATE
    _LAST_SCAN_MS = 0
    _LAST_ST_LOOKUP_MS = 0
    _ST_AGENT_ID = 0
    _LAST_SPIRITS = {}
    _LAST_ST_HP = None
    _RECENT_LOSSES.clear()
    _PRIORITY_TARGET_ID = 0
    _PRIORITY_UNTIL_MS = 0
    _PRIORITY_BONUS = 0
    _PRIORITY_REASON = ""
    _WATCH_TARGET_ID = 0
    _WATCH_UNTIL_MS = 0
    _LAST_LOG_STATE = (0, 0, "")
