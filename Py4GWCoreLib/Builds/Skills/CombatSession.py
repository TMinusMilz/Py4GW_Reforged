"""EXP36 Stage 12: explicit local combat-session identity.

Observation/infrastructure only.  The token is used to bound adaptive caches and
diagnostics; it deliberately does not replace the already-live shared-memory
claim keys in E07/E09/E11 in this stage.
"""
from __future__ import annotations

from dataclasses import dataclass

from Py4GWCoreLib.Map import Map
from Py4GWCoreLib.Party import Party


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    epoch: int
    map_id: int
    party_id: int
    hard_mode: bool
    map_ready: bool
    instance_uptime_ms: int
    reason: str = ""

    @property
    def key(self) -> str:
        return f"e{self.epoch}:m{self.map_id}:p{self.party_id}:hm{int(self.hard_mode)}"


_EPOCH = 0
_INITIALIZED = False
_LAST_READY = False
_LAST_MAP_ID = 0
_LAST_PARTY_ID = 0
_LAST_HARD_MODE = False
_LAST_UPTIME_MS = 0
_LAST_LOGGED_EPOCH = 0
_SESSION_DIAGNOSTIC_KEYS: set[str] = set()


def _safe_state() -> tuple[bool, int, int, bool, int]:
    try:
        ready = bool(Map.IsMapReady())
    except Exception:
        ready = False
    try:
        map_id = int(Map.GetMapID() or 0)
    except Exception:
        map_id = 0
    try:
        party_id = int(Party.GetPartyID() or 0)
    except Exception:
        party_id = 0
    try:
        hard_mode = bool(Party.IsHardMode())
    except Exception:
        hard_mode = False
    try:
        uptime = int(Map.GetInstanceUptime() or 0)
    except Exception:
        uptime = 0
    return ready, map_id, party_id, hard_mode, uptime


def _log_epoch(snapshot: SessionSnapshot) -> None:
    global _LAST_LOGGED_EPOCH
    if int(snapshot.epoch) <= 0 or int(snapshot.epoch) == int(_LAST_LOGGED_EPOCH):
        return
    _LAST_LOGGED_EPOCH = int(snapshot.epoch)
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
        CombatDebug.log_event(
            "HEROAI_SESSION_EPOCH",
            session_epoch=int(snapshot.epoch),
            session_key=str(snapshot.key),
            map_id=int(snapshot.map_id),
            party_id=int(snapshot.party_id),
            hard_mode=bool(snapshot.hard_mode),
            instance_uptime_ms=int(snapshot.instance_uptime_ms),
            reason=str(snapshot.reason or ""),
            policy="exp36_stage12_e17_explicit_instance_epoch_observation",
        )
    except Exception:
        pass


def refresh() -> SessionSnapshot:
    global _EPOCH, _INITIALIZED, _LAST_READY, _LAST_MAP_ID, _LAST_PARTY_ID
    global _LAST_HARD_MODE, _LAST_UPTIME_MS

    ready, map_id, party_id, hard_mode, uptime = _safe_state()
    reason = ""
    new_epoch = False
    if not _INITIALIZED:
        _INITIALIZED = True
        if ready and map_id > 0:
            new_epoch = True
            reason = "initial_ready_instance"
    elif ready and map_id > 0:
        if not _LAST_READY:
            new_epoch = True
            reason = "map_not_ready_to_ready"
        elif _LAST_MAP_ID > 0 and map_id != _LAST_MAP_ID:
            new_epoch = True
            reason = "map_id_changed"
        elif _LAST_PARTY_ID > 0 and party_id > 0 and party_id != _LAST_PARTY_ID:
            new_epoch = True
            reason = "party_id_changed"
        elif hard_mode != _LAST_HARD_MODE:
            new_epoch = True
            reason = "hard_mode_changed"
        elif _LAST_UPTIME_MS > 2500 and uptime >= 0 and uptime + 1500 < _LAST_UPTIME_MS:
            # Same-map re-entry is visible because the native instance timer resets.
            new_epoch = True
            reason = "instance_uptime_reset"

    if new_epoch:
        _EPOCH += 1
        _SESSION_DIAGNOSTIC_KEYS.clear()

    _LAST_READY = bool(ready)
    if ready and map_id > 0:
        _LAST_MAP_ID = int(map_id)
        _LAST_PARTY_ID = int(party_id)
        _LAST_HARD_MODE = bool(hard_mode)
        _LAST_UPTIME_MS = int(uptime)

    snap = SessionSnapshot(
        epoch=int(_EPOCH), map_id=int(map_id), party_id=int(party_id),
        hard_mode=bool(hard_mode), map_ready=bool(ready),
        instance_uptime_ms=int(uptime), reason=str(reason),
    )
    if new_epoch:
        _log_epoch(snap)
    return snap


def current() -> SessionSnapshot:
    return refresh()


def current_key() -> str:
    return refresh().key


def diagnostic_once(key: str) -> bool:
    """Return True once per local session epoch for a semantic diagnostic key."""
    refresh()
    token = str(key or "")
    if not token:
        return False
    if token in _SESSION_DIAGNOSTIC_KEYS:
        return False
    _SESSION_DIAGNOSTIC_KEYS.add(token)
    if len(_SESSION_DIAGNOSTIC_KEYS) > 1024:
        # Defensive bound; preserve current key if an unusually noisy caller exists.
        _SESSION_DIAGNOSTIC_KEYS.clear()
        _SESSION_DIAGNOSTIC_KEYS.add(token)
    return True
