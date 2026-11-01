"""Cross-account coordination for multiple Psychic Instability Mesmers.

Goals:
- Only one PI is allowed to commit into the same enemy packet at a time.
- Complicate ("Erschweren") is reserved team-wide per interrupted enemy skill so
  two PI Mesmers do not spend it on the same disable window.
- Cry of Frustration is reserved per enemy packet so the player PI Mesmer and
  PI hero do not both spend the same AoE interrupt on one cast cluster.
- Coordination uses the multibox shared-memory isolation group first and the
  native PartyID only as a compatibility fallback.

The leases are deliberately short. They only arbitrate the command race; the
actual knockdown/disable state in Guild Wars remains authoritative afterwards.
"""
from __future__ import annotations

from Py4GWCoreLib import GLOBAL_CACHE
from Py4GWCoreLib.Builds.Skills.DangerInterruptClaim import get_game_tick
from Py4GWCoreLib.Player import Player

PI_PACKET_LOCK_KIND = 101
COMPLICATE_SKILL_LOCK_KIND = 102
CRY_PACKET_LOCK_KIND = 103
PI_PACKET_RESERVATION_LOCK_ID = 0x50495253  # 'PIRS'
COMPLICATE_SKILL_RESERVATION_LOCK_ID = 0x434F4D50  # 'COMP'
CRY_PACKET_RESERVATION_LOCK_ID = 0x43525946  # 'CRYF'
PI_PACKET_RESERVATION_MS = 1500
COMPLICATE_SKILL_RESERVATION_MS = 2200
CRY_PACKET_RESERVATION_MS = 1100


def _owner_group_candidates() -> tuple[int, ...]:
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

    try:
        gid = int(GLOBAL_CACHE.Party.GetPartyID() or 0)
        if gid > 0:
            groups.append(gid)
    except Exception:
        pass

    out: list[int] = []
    for gid in groups:
        if gid > 0 and gid not in out:
            out.append(gid)
    return tuple(out)


def _owner_context() -> tuple[str, int]:
    try:
        email = str(Player.GetAccountEmail() or "").strip()
    except Exception:
        email = ""
    groups = _owner_group_candidates()
    return email, int(groups[0] if groups else 0)


def _log(event: str, **fields) -> None:
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(str(event), **fields)
    except Exception:
        pass




def _intent_snapshot(*, fresh: bool = False):
    """Return active intents; reuse Apo's per-frame snapshot for read-only probes.

    Reservation winner verification must request ``fresh=True`` because it runs
    immediately after PostLock and therefore must observe the just-written row.
    """
    try:
        shmem = GLOBAL_CACHE.ShMem
        if not fresh and hasattr(shmem, "GetAllIntents"):
            return tuple(shmem.GetAllIntents() or ())
        return tuple(shmem.GetAllAccounts().GetAllIntents() or ())
    except Exception:
        return ()


def _active_locks(kind_id: int, lock_skill_id: int, target_ids: set[int] | None = None, *, fresh: bool = False):
    """Return live matching SKILL_TARGET locks from our multibox group."""
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, primary_gid = _owner_context()
        groups = set(_owner_group_candidates())
        if primary_gid > 0:
            groups.add(primary_gid)
        now = int(get_game_tick() or 0)
        if not email or now <= 0:
            return []
        out = []
        for slot, intent in _intent_snapshot(fresh=bool(fresh)):
            try:
                if int(getattr(intent, "ExpiresAtTick", 0) or 0) <= now:
                    continue
                if int(getattr(intent, "KindID", 0) or 0) != int(kind_id):
                    continue
                if int(getattr(intent, "SkillID", 0) or 0) != int(lock_skill_id):
                    continue
                gid = int(getattr(intent, "IsolationGroupID", 0) or 0)
                if groups and gid not in groups:
                    continue
                target = int(getattr(intent, "TargetAgentID", 0) or 0)
                if target_ids is not None and target not in target_ids:
                    continue
                out.append((int(slot), intent))
            except Exception:
                continue
        return out
    except Exception:
        return []


def _clear_owned(kind_id: int, target_ids: tuple[int, ...] | list[int] | set[int]) -> None:
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import WhiteboardLockKind
        email, gid = _owner_context()
        if not email or gid <= 0:
            return
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccounts()
        for target_id in {int(x) for x in target_ids if int(x or 0) > 0}:
            try:
                all_accounts.ClearLockByOwnerKindTarget(
                    email, int(kind_id), int(target_id), int(gid)
                )
            except Exception:
                continue
    except Exception:
        pass


def _reserve_targets(kind_id: int, lock_skill_id: int, target_ids: list[int] | tuple[int, ...], duration_ms: int) -> bool:
    targets = tuple(sorted({int(x) for x in target_ids if int(x or 0) > 0}))
    if not targets:
        return False
    try:
        from Py4GWCoreLib.enums_src.Whiteboard_enums import (
            WhiteboardClaimStrength,
            WhiteboardLockKind,
            WhiteboardLockMode,
            WhiteboardReentryPolicy,
        )
        email, gid = _owner_context()
        now = int(get_game_tick() or 0)
        if not email or gid <= 0 or now <= 0:
            # Fail open when shared memory is unavailable. Exact interrupt claims
            # still provide local/cross-account protection where possible.
            return True

        # Fast pre-check. If another account already owns any member of this
        # packet/skill, do not race it with a second PI/Complicate.
        existing = _active_locks(int(kind_id), int(lock_skill_id), set(targets))
        if any(str(getattr(intent, "OwnerEmail", "") or "") != email for _slot, intent in existing):
            return False

        posted: list[int] = []
        expires = now + max(700, int(duration_ms))
        for target in targets:
            slot = int(GLOBAL_CACHE.ShMem.PostLock(
                email,
                int(kind_id),
                int(lock_skill_id),
                int(target),
                int(expires),
                int(gid),
                int(WhiteboardLockMode.EXCLUSIVE),
                1,
                int(WhiteboardReentryPolicy.NON_REENTRANT),
                int(WhiteboardClaimStrength.HARD),
            ))
            if slot == -1:
                _clear_owned(int(kind_id), posted)
                return False
            posted.append(int(target))

        # Verify deterministic winner after posting. Shared-memory posting is not
        # an atomic check-and-set across injected clients, so compare all live
        # overlapping leases and keep the earliest owner only.
        live = _active_locks(int(kind_id), int(lock_skill_id), set(targets), fresh=True)
        foreign_or_ours = []
        for slot, intent in live:
            foreign_or_ours.append((
                int(getattr(intent, "PostedAtTick", 0) or 0),
                str(getattr(intent, "OwnerEmail", "") or ""),
                int(slot),
            ))
        if foreign_or_ours:
            winner = min(foreign_or_ours)
            if winner[1] and winner[1] != email:
                _clear_owned(int(kind_id), targets)
                return False
        return True
    except Exception:
        return True


def pi_packet_reserved(member_ids: list[int] | tuple[int, ...]) -> bool:
    targets = {int(x) for x in member_ids if int(x or 0) > 0}
    if not targets:
        return False
    try:
        email, _gid = _owner_context()
        for _slot, intent in _active_locks(PI_PACKET_LOCK_KIND, PI_PACKET_RESERVATION_LOCK_ID, targets):
            owner = str(getattr(intent, "OwnerEmail", "") or "")
            if owner and owner != email:
                return True
        return False
    except Exception:
        return False


def reserve_pi_packet(member_ids: list[int] | tuple[int, ...], *, duration_ms: int = PI_PACKET_RESERVATION_MS) -> bool:
    targets = tuple(sorted({int(x) for x in member_ids if int(x or 0) > 0}))
    ok = _reserve_targets(PI_PACKET_LOCK_KIND, PI_PACKET_RESERVATION_LOCK_ID, targets, int(duration_ms))
    _log(
        "PI_PACKET_RESERVATION" if ok else "PI_PACKET_RESERVATION_BLOCKED",
        packet_size=len(targets), members=",".join(map(str, targets)), duration_ms=int(duration_ms),
    )
    return bool(ok)


def release_pi_packet(member_ids: list[int] | tuple[int, ...], *, reason: str = "not_fired") -> None:
    targets = tuple(sorted({int(x) for x in member_ids if int(x or 0) > 0}))
    _clear_owned(PI_PACKET_LOCK_KIND, targets)
    _log("PI_PACKET_RESERVATION_RELEASED", packet_size=len(targets), reason=str(reason))


def complicate_skill_reserved(enemy_skill_id: int) -> bool:
    sid = int(enemy_skill_id or 0)
    if sid <= 0:
        return False
    try:
        email, _gid = _owner_context()
        for _slot, intent in _active_locks(COMPLICATE_SKILL_LOCK_KIND, COMPLICATE_SKILL_RESERVATION_LOCK_ID, {sid}):
            owner = str(getattr(intent, "OwnerEmail", "") or "")
            if owner and owner != email:
                return True
        return False
    except Exception:
        return False


def reserve_complicate_skill(enemy_skill_id: int, *, duration_ms: int = COMPLICATE_SKILL_RESERVATION_MS) -> bool:
    sid = int(enemy_skill_id or 0)
    if sid <= 0:
        return False
    ok = _reserve_targets(COMPLICATE_SKILL_LOCK_KIND, COMPLICATE_SKILL_RESERVATION_LOCK_ID, (sid,), int(duration_ms))
    _log(
        "COMPLICATE_SKILL_RESERVATION" if ok else "COMPLICATE_SKILL_RESERVATION_BLOCKED",
        enemy_skill_id=sid, duration_ms=int(duration_ms),
    )
    return bool(ok)


def release_complicate_skill(enemy_skill_id: int, *, reason: str = "not_fired") -> None:
    sid = int(enemy_skill_id or 0)
    if sid <= 0:
        return
    _clear_owned(COMPLICATE_SKILL_LOCK_KIND, (sid,))
    _log("COMPLICATE_SKILL_RESERVATION_RELEASED", enemy_skill_id=sid, reason=str(reason))

def cry_packet_reserved(member_ids: list[int] | tuple[int, ...]) -> bool:
    targets = {int(x) for x in member_ids if int(x or 0) > 0}
    if not targets:
        return False
    try:
        email, _gid = _owner_context()
        for _slot, intent in _active_locks(CRY_PACKET_LOCK_KIND, CRY_PACKET_RESERVATION_LOCK_ID, targets):
            owner = str(getattr(intent, "OwnerEmail", "") or "")
            if owner and owner != email:
                return True
        return False
    except Exception:
        return False


def reserve_cry_packet(member_ids: list[int] | tuple[int, ...], *, duration_ms: int = CRY_PACKET_RESERVATION_MS) -> bool:
    targets = tuple(sorted({int(x) for x in member_ids if int(x or 0) > 0}))
    ok = _reserve_targets(CRY_PACKET_LOCK_KIND, CRY_PACKET_RESERVATION_LOCK_ID, targets, int(duration_ms))
    _log(
        "CRY_PACKET_RESERVATION" if ok else "CRY_PACKET_RESERVATION_BLOCKED",
        packet_size=len(targets), members=",".join(map(str, targets)), duration_ms=int(duration_ms),
    )
    return bool(ok)


def release_cry_packet(member_ids: list[int] | tuple[int, ...], *, reason: str = "not_fired") -> None:
    targets = tuple(sorted({int(x) for x in member_ids if int(x or 0) > 0}))
    _clear_owned(CRY_PACKET_LOCK_KIND, targets)
    _log("CRY_PACKET_RESERVATION_RELEASED", packet_size=len(targets), reason=str(reason))

