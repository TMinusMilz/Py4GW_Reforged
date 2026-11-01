"""Lightweight cross-account coordination for the optional burning combos.

Two combinations are supported, but each remains useful on its own:

1) Spirit Burn -> Epidemic
   No whiteboard dependency is required.  The SoJ Monk burns a high-value
   central packet member; the PI Mesmer simply looks for a burning member of
   the same shared packet and spreads its current conditions with Epidemic.

2) Bed of Coals -> Psychic Instability
   A Fire hero can be controlled from a different game client than the PI
   Mesmer.  Bed of Coals therefore publishes a short whiteboard coverage lease
   for the current packet.  PI gives casting enemies inside that leased packet
   first refusal for its knockdown.  If whiteboard access is unavailable both
   skills fail open and continue to work independently.

The module deliberately carries no polling loop or heavy telemetry.
"""
from __future__ import annotations

from Py4GWCoreLib import GLOBAL_CACHE
from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import get_game_tick
from Py4GWCoreLib.Player import Player

BED_OF_COALS_PACKET_LOCK_ID = 0x42434F41  # 'BCOA'
PI_KNOCKDOWN_PACKET_LOCK_ID = 0x50494B44  # 'PIKD'
PI_KNOCKDOWN_ANCHOR_LOCK_ID = 0x5049414E  # 'PIAN'
SPIRIT_BURN_CARRIER_LOCK_ID = 0x53425552  # 'SBUR'
BED_OF_COALS_COVERAGE_MS = 4300
PI_KNOCKDOWN_COVERAGE_MS = 4600
SPIRIT_BURN_COVERAGE_MS = 3400


def _owner_group_candidates() -> tuple[int, ...]:
    """Return every plausible shared-memory isolation group for this client.

    Cross-account combo leases must use the ShMem isolation group, not assume
    the native PartyID is identical on every injected client.  Older versions
    preferred PartyID and therefore could publish a PI lease that the Fire
    hero account never considered part of its group.
    """
    groups: list[int] = []
    try:
        email = str(Player.GetAccountEmail() or "").strip()
    except Exception:
        email = ""

    if email:
        try:
            gid = int(GLOBAL_CACHE.ShMem.GetAccountGroupByEmail(email) or 0)
            if gid > 0:
                groups.append(gid)
        except Exception:
            pass
        try:
            for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                if str(getattr(account, "AccountEmail", "") or "").strip() != email:
                    continue
                gid = int(getattr(account, "IsolationGroupID", 0) or 0)
                if gid > 0:
                    groups.append(gid)
                break
        except Exception:
            pass

    # PartyID remains a compatibility fallback, but no longer wins over the
    # actual shared-memory isolation group used by the multibox whiteboard.
    try:
        gid = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
        if gid > 0:
            groups.append(gid)
    except Exception:
        pass

    deduped: list[int] = []
    for gid in groups:
        if gid > 0 and gid not in deduped:
            deduped.append(gid)
    return tuple(deduped)


def _owner_context() -> tuple[str, int]:
    try:
        email = str(Player.GetAccountEmail() or "").strip()
        if not email:
            return "", 0
        groups = _owner_group_candidates()
        return email, int(groups[0] if groups else 0)
    except Exception:
        return "", 0


def coordination_group_candidates() -> tuple[int, ...]:
    """Public diagnostic view used by the Fire/PI hero controllers."""
    return _owner_group_candidates()


def publish_bed_of_coals_packet(member_ids: list[int] | tuple[int, ...], *, duration_ms: int = BED_OF_COALS_COVERAGE_MS) -> int:
    """Publish short packet-member leases after a Bed of Coals cast request."""
    members = sorted({int(x) for x in member_ids if int(x or 0) > 0})
    if not members:
        return 0
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength,
            WhiteboardLockKind,
            WhiteboardLockMode,
            WhiteboardReentryPolicy,
        )
        email, gid = _owner_context()
        groups = set(_owner_group_candidates())
        if gid > 0:
            groups.add(int(gid))
        now = int(get_game_tick() or 0)
        if not email or now <= 0:
            return 0
        posted = 0
        for enemy_id in members:
            try:
                slot = int(GLOBAL_CACHE.ShMem.PostLock(
                    email,
                    int(WhiteboardLockKind.SKILL_TARGET),
                    int(BED_OF_COALS_PACKET_LOCK_ID),
                    int(enemy_id),
                    int(now) + max(1000, int(duration_ms)),
                    int(gid),
                    int(WhiteboardLockMode.EXCLUSIVE),
                    1,
                    int(WhiteboardReentryPolicy.NON_REENTRANT),
                    int(WhiteboardClaimStrength.HARD),
                ))
                posted += int(slot != -1)
            except Exception:
                continue
        return int(posted)
    except Exception:
        return 0


def active_bed_of_coals_members(current_member_ids: list[int] | tuple[int, ...]) -> tuple[int, ...]:
    """Return current packet members covered by a live Bed-of-Coals lease."""
    current = {int(x) for x in current_member_ids if int(x or 0) > 0}
    if not current:
        return ()
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        groups = set(_owner_group_candidates())
        if gid > 0:
            groups.add(int(gid))
        now = int(get_game_tick() or 0)
        if not email or now <= 0:
            return ()
        covered: set[int] = set()
        for _slot, intent in GLOBAL_CACHE.ShMem.GetAllAccounts().GetAllIntents() or []:
            try:
                if int(getattr(intent, "ExpiresAtTick", 0) or 0) <= now:
                    continue
                if int(getattr(intent, "KindID", 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET):
                    continue
                if int(getattr(intent, "SkillID", 0) or 0) != int(BED_OF_COALS_PACKET_LOCK_ID):
                    continue
                intent_gid = int(getattr(intent, "IsolationGroupID", 0) or 0)
                if groups and intent_gid not in groups:
                    continue
                target = int(getattr(intent, "TargetAgentID", 0) or 0)
                if target in current:
                    covered.add(target)
            except Exception:
                continue
        return tuple(sorted(covered))
    except Exception:
        return ()


def publish_spirit_burn_carrier(target_id: int, *, duration_ms: int = SPIRIT_BURN_COVERAGE_MS) -> int:
    """Publish a *confirmed* Spirit Burn carrier for Epidemic.

    EXP35 changes the contract: the SoJ owner must first prove locally that
    Spirit Burn was consumed and that a previously non-burning target acquired
    Burning. Consumers may therefore treat this short lease as combo proof
    instead of as a mere cast intention.
    """
    target_id = int(target_id or 0)
    if target_id <= 0:
        return 0
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode, WhiteboardReentryPolicy,
        )
        email, gid = _owner_context()
        groups = set(_owner_group_candidates())
        if gid > 0:
            groups.add(int(gid))
        now = int(get_game_tick() or 0)
        if not email or now <= 0:
            return 0
        slot = int(GLOBAL_CACHE.ShMem.PostLock(
            email, int(WhiteboardLockKind.SKILL_TARGET), int(SPIRIT_BURN_CARRIER_LOCK_ID), target_id,
            now + max(1200, int(duration_ms)), int(gid), int(WhiteboardLockMode.EXCLUSIVE), 1,
            int(WhiteboardReentryPolicy.NON_REENTRANT), int(WhiteboardClaimStrength.HARD),
        ))
        return int(slot != -1)
    except Exception:
        return 0


def active_spirit_burn_carriers(current_member_ids: list[int] | tuple[int, ...]) -> tuple[int, ...]:
    current = {int(x) for x in current_member_ids if int(x or 0) > 0}
    if not current:
        return ()
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        groups = set(_owner_group_candidates())
        if gid > 0:
            groups.add(int(gid))
        now = int(get_game_tick() or 0)
        if not email or now <= 0:
            return ()
        covered=set()
        for _slot, intent in GLOBAL_CACHE.ShMem.GetAllAccounts().GetAllIntents() or []:
            try:
                if int(getattr(intent, 'ExpiresAtTick', 0) or 0) <= now: continue
                if int(getattr(intent, 'KindID', 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET): continue
                if int(getattr(intent, 'SkillID', 0) or 0) != int(SPIRIT_BURN_CARRIER_LOCK_ID): continue
                intent_gid = int(getattr(intent, 'IsolationGroupID', 0) or 0)
                if groups and intent_gid not in groups: continue
                target=int(getattr(intent, 'TargetAgentID', 0) or 0)
                if target in current: covered.add(target)
            except Exception:
                continue
        return tuple(sorted(covered))
    except Exception:
        return ()


def _active_skill_target_leases(skill_id: int) -> tuple[int, ...]:
    """Return all live shared-memory targets for one lightweight combo lease.

    This deliberately does not require the caller's current cluster membership.
    Different clients may resolve the same moving packet to a different anchor,
    so the consumer validates range/alive/condition locally after reading it.
    """
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        groups = set(_owner_group_candidates())
        if gid > 0:
            groups.add(int(gid))
        now = int(get_game_tick() or 0)
        if not email or now <= 0:
            return ()
        covered: set[int] = set()
        for _slot, intent in GLOBAL_CACHE.ShMem.GetAllAccounts().GetAllIntents() or []:
            try:
                if int(getattr(intent, "ExpiresAtTick", 0) or 0) <= now:
                    continue
                if int(getattr(intent, "KindID", 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET):
                    continue
                if int(getattr(intent, "SkillID", 0) or 0) != int(skill_id):
                    continue
                intent_gid = int(getattr(intent, "IsolationGroupID", 0) or 0)
                if groups and intent_gid not in groups:
                    continue
                target = int(getattr(intent, "TargetAgentID", 0) or 0)
                if target > 0:
                    covered.add(target)
            except Exception:
                continue
        return tuple(sorted(covered))
    except Exception:
        return ()


def active_bed_of_coals_targets() -> tuple[int, ...]:
    return _active_skill_target_leases(BED_OF_COALS_PACKET_LOCK_ID)


def publish_pi_knockdown_packet(
    member_ids: list[int] | tuple[int, ...], *, anchor_id: int = 0,
    duration_ms: int = PI_KNOCKDOWN_COVERAGE_MS,
) -> int:
    """Publish one confirmed Psychic Instability event.

    Member leases describe the packet that PI is expected to knock down.  A
    second, separate anchor lease records the *exact enemy PI was cast on*.
    RoJ consumers can therefore stack a short 2-3-ray burst on the same PI
    center instead of reconstructing an arbitrary center from a moving packet.

    The return value intentionally remains the number of packet-member leases
    for backwards compatibility with the Fire-controller diagnostics.
    """
    members = sorted({int(x) for x in member_ids if int(x or 0) > 0})
    anchor_id = int(anchor_id or 0)
    if anchor_id > 0 and anchor_id not in members:
        members.append(anchor_id)
        members.sort()
    if not members:
        return 0
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength, WhiteboardLockKind, WhiteboardLockMode, WhiteboardReentryPolicy,
        )
        email, gid = _owner_context()
        now = int(get_game_tick() or 0)
        if not email or now <= 0:
            return 0
        expires = now + max(1200, int(duration_ms))
        posted = 0
        for enemy_id in members:
            try:
                slot = int(GLOBAL_CACHE.ShMem.PostLock(
                    email, int(WhiteboardLockKind.SKILL_TARGET), int(PI_KNOCKDOWN_PACKET_LOCK_ID),
                    int(enemy_id), int(expires), int(gid),
                    int(WhiteboardLockMode.EXCLUSIVE), 1, int(WhiteboardReentryPolicy.NON_REENTRANT),
                    int(WhiteboardClaimStrength.HARD),
                ))
                posted += int(slot != -1)
            except Exception:
                continue
        if anchor_id > 0:
            try:
                GLOBAL_CACHE.ShMem.PostLock(
                    email, int(WhiteboardLockKind.SKILL_TARGET), int(PI_KNOCKDOWN_ANCHOR_LOCK_ID),
                    int(anchor_id), int(expires), int(gid),
                    int(WhiteboardLockMode.EXCLUSIVE), 1, int(WhiteboardReentryPolicy.NON_REENTRANT),
                    int(WhiteboardClaimStrength.HARD),
                )
            except Exception:
                pass
        return int(posted)
    except Exception:
        return 0


def active_pi_knockdown_targets() -> tuple[int, ...]:
    """Return all live PI packet-member leases visible to this account group."""
    return _active_skill_target_leases(PI_KNOCKDOWN_PACKET_LOCK_ID)


def active_pi_knockdown_event() -> tuple[int, tuple[int, ...], int]:
    """Return the newest PI event as (exact_anchor, packet_members, expires_at).

    Matching by expiry keeps overlapping PI events separate.  Older consumers
    may continue to use :func:`active_pi_knockdown_targets`.
    """
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        groups = set(_owner_group_candidates())
        if gid > 0:
            groups.add(int(gid))
        now = int(get_game_tick() or 0)
        if not email or now <= 0:
            return 0, (), 0
        anchors: list[tuple[int, int]] = []
        packets: list[tuple[int, int]] = []
        for _slot, intent in GLOBAL_CACHE.ShMem.GetAllAccounts().GetAllIntents() or []:
            try:
                expires = int(getattr(intent, "ExpiresAtTick", 0) or 0)
                if expires <= now:
                    continue
                if int(getattr(intent, "KindID", 0) or 0) != int(WhiteboardLockKind.SKILL_TARGET):
                    continue
                intent_gid = int(getattr(intent, "IsolationGroupID", 0) or 0)
                if groups and intent_gid not in groups:
                    continue
                skill_id = int(getattr(intent, "SkillID", 0) or 0)
                target = int(getattr(intent, "TargetAgentID", 0) or 0)
                if target <= 0:
                    continue
                if skill_id == int(PI_KNOCKDOWN_ANCHOR_LOCK_ID):
                    anchors.append((expires, target))
                elif skill_id == int(PI_KNOCKDOWN_PACKET_LOCK_ID):
                    packets.append((expires, target))
            except Exception:
                continue
        if not anchors:
            # Backwards-compatible fallback for an older publisher.
            members = tuple(sorted({target for _expires, target in packets}))
            return 0, members, max((expires for expires, _target in packets), default=0)
        anchors.sort(reverse=True)
        expires, anchor = anchors[0]
        # A single publish call gives anchor and members the exact same expiry.
        # Allow a tiny tolerance for runtimes that quantize one PostLock call.
        members = tuple(sorted({
            target for member_expires, target in packets
            if abs(int(member_expires) - int(expires)) <= 120
        }))
        if anchor not in members:
            members = tuple(sorted(set(members) | {int(anchor)}))
        return int(anchor), members, int(expires)
    except Exception:
        return 0, (), 0


def active_spirit_burn_targets() -> tuple[int, ...]:
    return _active_skill_target_leases(SPIRIT_BURN_CARRIER_LOCK_ID)
