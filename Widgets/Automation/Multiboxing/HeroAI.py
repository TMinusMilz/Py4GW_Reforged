#region Imports
import math
import os
import sys
import traceback
import Py4GW
import PyImGui

from Py4GWCoreLib.Builds.Any.HeroAI import HeroAI_Build

MODULE_NAME = "HeroAI"
MODULE_ICON = "Assets/Textures/Module_Icons/HeroAI.png"

from Py4GWCoreLib.Map import Map
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.routines_src.BehaviourTrees import BehaviorTree

from Py4GWCoreLib.HeroAI.cache_data import CacheData
from Py4GWCoreLib.HeroAI.follow.follower_runtime import (
    FollowExecutionState,
    execute_follower_follow,
    get_follow_destination_distance,
    is_follow_recovery_active,
)
from Py4GWCoreLib.HeroAI import enemy_party
from Py4GWCoreLib.HeroAI import resurrection_scroll

from Py4GWCoreLib.HeroAI.windows import (HeroAI_FloatingWindows ,HeroAI_Windows,)
from Py4GWCoreLib.HeroAI.ui_base import HeroAI_BaseUI
from Py4GWCoreLib.HeroAI.ui import (draw_configure_window, draw_skip_cutscene_overlay)
from Py4GWCoreLib.HeroAI import team_viewer_broadcast
from Py4GWCoreLib import (GLOBAL_CACHE, Agent, Party,
                          Range, Routines, ThrottledTimer, SharedCommandType)

#region GLOBALS
LOOT_THROTTLE_CHECK = ThrottledTimer(250)

cached_data = CacheData()
heroai_build = HeroAI_Build(cached_data)
map_quads : list[Map.Pathing.Quad] = []
#region Looting
def LootingNode(cached_data: CacheData)-> BehaviorTree.NodeState:
    options = cached_data.account_options
    if not options or not options.Looting:
        return BehaviorTree.NodeState.FAILURE

    if is_follow_recovery_active(cached_data, follow_execution_state):
        return BehaviorTree.NodeState.FAILURE
    
    if cached_data.data.in_aggro:
        return BehaviorTree.NodeState.FAILURE
    
    
    account_email = Player.GetAccountEmail()
    index, message = GLOBAL_CACHE.ShMem.PreviewNextMessage(account_email)

    if index != -1 and message and message.Command == SharedCommandType.PickUpLoot:
        if LOOT_THROTTLE_CHECK.IsExpired():
            return BehaviorTree.NodeState.FAILURE
        return BehaviorTree.NodeState.RUNNING
    
    if GLOBAL_CACHE.Inventory.GetFreeSlotCount() <= 1:
        return BehaviorTree.NodeState.FAILURE
    
    from Py4GWCoreLib.py4gwcorelib_src.system_settings.loot_filters import LootFilters

    loot_array = LootFilters().GetLootArray(Range.Earshot.value)

    if len(loot_array) == 0:
        return BehaviorTree.NodeState.FAILURE

    self_account = GLOBAL_CACHE.ShMem.GetAccountDataFromEmail(account_email)
    if self_account:
        GLOBAL_CACHE.ShMem.SendMessage(
            self_account.AccountEmail,
            self_account.AccountEmail,
            SharedCommandType.PickUpLoot,
            (0, 0, 0, 0),
        )
        LOOT_THROTTLE_CHECK.Reset()
        # Return RUNNING so the tree knows the task started
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree.NodeState.FAILURE




#region Combat
def _team_recovery_support_hold_active() -> bool:
    try:
        from Py4GWCoreLib.Builds.Skills import TeamRecovery
        return bool(TeamRecovery.is_support_hold_active())
    except Exception:
        return False

def HandleOutOfCombat(cached_data: CacheData):
    options = cached_data.account_options
    
    if not options or not options.Combat:  # halt operation if combat is disabled
        heroai_build.ResetTickExecution()
        return False
    
    if cached_data.data.in_aggro:
        return False

    if is_follow_recovery_active(cached_data, follow_execution_state) and not _team_recovery_support_hold_active():
        heroai_build.ResetTickExecution()
        return False

    player_agent_id = Player.GetAgentID()
    if cached_data.combat_handler.InCastingRoutine() or Agent.IsCasting(player_agent_id):
        return False

    heroai_build.set_cached_data(cached_data)
    next(heroai_build.Tick(is_in_combat=False), None)
    return heroai_build.DidTickSucceed()

def HandleCombat(cached_data: CacheData):
    options = cached_data.account_options
    
    if not options or not options.Combat:  # halt operation if combat is disabled
        heroai_build.ResetTickExecution()
        return False

    if is_follow_recovery_active(cached_data, follow_execution_state) and not _team_recovery_support_hold_active():
        heroai_build.ResetTickExecution()
        return False
    
    if not cached_data.data.in_aggro:
        return False

    heroai_build.set_cached_data(cached_data)
    next(heroai_build.Tick(is_in_combat=True), None)
    return heroai_build.DidTickSucceed()



#region Following
following_flag = False
follow_execution_state = FollowExecutionState()
FOLLOW_INI_FILENAMES = (
    "FollowModule_Formations.ini",
    "FollowModule_Settings.ini",
)
printed_widget_list = False

def _follow_ini_paths() -> list[str]:
    base_path = os.path.join(
        PySystem.Console.get_projects_path(),
        "Settings",
        "Global",
        "HeroAI",
    )
    return [os.path.join(base_path, filename) for filename in FOLLOW_INI_FILENAMES]

def _follow_ini_ready() -> bool:
    return all(os.path.exists(path) for path in _follow_ini_paths())

def EnsureFollowModuleIni() -> None:
    if _follow_ini_ready():
        return

    try:
        from Py4GWCoreLib.HeroAI.follow.editor import _init_once
        _init_once()
    except Exception as e:
        PySystem.Console.Log(MODULE_NAME, f"Follow formation INI bootstrap failed: {e}", PySystem.Console.MessageType.Error)

def _build_cast_window_active() -> bool:
    # The active specialized build owns this timer, not the generic HeroAI
    # fallback. Never resolve/replace a build merely for the follow check.
    build = heroai_build.GetBuildContract()
    if build is None:
        build = heroai_build
    check = getattr(build, "HasLocalCastPending", None)
    return bool(check()) if callable(check) else False


def Follow(cached_data: CacheData) -> BehaviorTree.NodeState:
    if not cached_data.data.is_leader:
        return execute_follower_follow(
            cached_data, follow_execution_state,
            cast_pending=_build_cast_window_active,
        )
    
    return BehaviorTree.NodeState.FAILURE  # leader doesn't follow anyone

def handle_UI (cached_data: CacheData):
    global HeroAI_BT
    team_viewer_broadcast.tick()
    if not cached_data.ui_state_data.show_classic_controls:
        HeroAI_BaseUI.DrawEmbeddedWindow(cached_data)
    else:
        HeroAI_BaseUI.DrawControlPanelWindow(cached_data)
        if HeroAI_FloatingWindows.settings.ShowPartyPanelUI:
            HeroAI_BaseUI.DrawFollowerUI(cached_data)

    if HeroAI_BaseUI.show_debug:
        HeroAI_BaseUI.draw_debug_window(HeroAI_BT)

    HeroAI_FloatingWindows.show_ui(cached_data)
    if Map.IsExplorable() and cached_data.data.is_leader and enemy_party.is_enabled():
        enemy_party.ui_main()
    HeroAI_BaseUI.DrawBuildMatchesWindow(cached_data)
    HeroAI_BaseUI.DrawFollowFormationsQuickWindow(cached_data)
   
def initialize(cached_data: CacheData) -> bool:
    if not Routines.Checks.Map.MapValid():
        heroai_build.ClearBuildContract()
        return False
    
    if not GLOBAL_CACHE.Party.IsPartyLoaded():
        heroai_build.ResetTickExecution()
        return False
        
    if not Map.IsExplorable():  # halt operation if not in explorable area
        heroai_build.ClearBuildContract()
        return False

    if Map.IsInCinematic():  # halt operation during cinematic
        heroai_build.ResetTickExecution()
        return False
    
    HeroAI_BaseUI._process_flagging_runtime(cached_data)
    #HeroAI_FloatingWindows.draw_Targeting_floating_buttons(cached_data)     
    heroai_build.set_cached_data(cached_data)
    player_agent_id = Player.GetAgentID()
    if not Agent.IsAlive(player_agent_id) or Agent.IsKnockedDown(player_agent_id):
        heroai_build.ResetTickExecution()
    cached_data.UpdateCombat()
    return True

        
#region main  
#DEPRECATED FOR BEHAVIOUR TREE IMPLEMENTATION
#KEPT FOR REFERENCE
"""def UpdateStatus(cached_data: CacheData) -> bool:
    
    if (
            not Agent.IsAlive(Player.GetAgentID())
            or (HeroAI_FloatingWindows.DistanceToDestination(cached_data) >= Range.SafeCompass.value)
            or Agent.IsKnockedDown(Player.GetAgentID())
            or cached_data.combat_handler.InCastingRoutine()
            or Agent.IsCasting(Player.GetAgentID())
        ):
            return False

    
    if LootingRoutineActive():
        return True

    if HandleOutOfCombat(cached_data):
        return True

    if Agent.IsMoving(Player.GetAgentID()):
        return False

    if Loot(cached_data):
        return True

    if Follow(cached_data):
        cached_data.follow_throttle_timer.Reset()
        return True

    if HandleCombat(cached_data):
        cached_data.auto_attack_timer.Reset()
        return True

    return False"""

def IsUserInterrupting() -> bool:
    from Py4GWCoreLib.enums_src.IO_enums import Key
    io = PyImGui.get_io()
    
    if io.want_capture_keyboard or io.want_capture_mouse:
        return False
    
    movement_keys = [
        Key.W.value, Key.A.value, Key.S.value, Key.D.value,
        Key.Q.value, Key.E.value, Key.Z.value, Key.R.value,
        Key.UpArrow.value, Key.DownArrow.value, 
        Key.LeftArrow.value, Key.RightArrow.value
    ]
    
    for vk in movement_keys:
        if PyImGui.is_key_down(vk):
            return True

    if (PyImGui.is_mouse_down(0) and PyImGui.is_mouse_down(1)) or PyImGui.is_mouse_down(2):
        return True

    return False
    
    
GlobalGuardNode = BehaviorTree.SequenceNode(
    name="GlobalGuard",
    children=[
        BehaviorTree.ConditionNode(
            name="IsAlive",
            condition_fn=lambda:
                Agent.IsAlive(Player.GetAgentID())
        ),

        BehaviorTree.ConditionNode(
            name="DistanceSafe",
            condition_fn=lambda:
                get_follow_destination_distance(cached_data) < Range.SafeCompass.value
                or is_follow_recovery_active(cached_data, follow_execution_state)
        ),

        BehaviorTree.ConditionNode(
            name="NotKnockedDown",
            condition_fn=lambda:
                not Agent.IsKnockedDown(Player.GetAgentID())
        ),
        
    ],
)
  
CastingBlockNode = BehaviorTree.ConditionNode(
    name="IsCasting",
    condition_fn=lambda:
        BehaviorTree.NodeState.RUNNING
        if (
            (
                cached_data.combat_handler.InCastingRoutine()
                and not is_follow_recovery_active(cached_data, follow_execution_state)
            )
            or Agent.IsCasting(Player.GetAgentID())
        )
        else BehaviorTree.NodeState.SUCCESS
)

    
    
def movement_interrupt() -> BehaviorTree.NodeState:
    # During a smart unstuck detour, BT.Move must be ticked at full HeroAI
    # BT rate so it can steer the engine target continuously 
    if follow_execution_state.stuck.mode != "idle":
        return BehaviorTree.NodeState.FAILURE  # let Follow run every tick during detour
    if Agent.IsMoving(Player.GetAgentID()):
        return BehaviorTree.NodeState.SUCCESS   # block lower-priority automation for this tick
    return BehaviorTree.NodeState.FAILURE      # allow next branch


def user_interrupt() -> BehaviorTree.NodeState:
    #if IsUserInterrupting():
    #    return BehaviorTree.NodeState.SUCCESS   # block lower-priority automation for this tick
    return BehaviorTree.NodeState.FAILURE      # allow next branch


HeroAI_BT = BehaviorTree.SequenceNode(name="HeroAI_Main_BT",
    children=[
        # ---------- GLOBAL HARD GUARD ----------
        GlobalGuardNode,
        CastingBlockNode,

        # ---------- PRIORITY SELECTOR ----------
        BehaviorTree.SelectorNode(name="UpdateStatusSelector",
            children=[
                # Looting routine already active (allowed anytime)
                BehaviorTree.ActionNode(name="LootingRoutine",
                    action_fn=lambda: LootingNode(cached_data),
                ),

                # Out-of-combat behavior (allowed while moving)
                BehaviorTree.ActionNode(
                    name="HandleOutOfCombat",
                    action_fn=lambda: (
                        BehaviorTree.NodeState.SUCCESS
                        if HandleOutOfCombat(cached_data)
                        else BehaviorTree.NodeState.FAILURE
                    ),
                ),

                # User / external movement override (blocks below)
                BehaviorTree.ActionNode(
                    name="UserInterrupt",
                    action_fn=lambda: user_interrupt(),
                ),

                # Follow
                BehaviorTree.ActionNode(
                    name="Follow",
                    action_fn=lambda: Follow(cached_data),
                ),

                BehaviorTree.ActionNode(
                    name="MovementInterrupt",
                    action_fn=lambda: movement_interrupt(),
                ),

                # Combat
                BehaviorTree.ActionNode(
                    name="HandleCombat",
                    action_fn=lambda: (
                        cached_data.auto_attack_timer.Reset()
                        or BehaviorTree.NodeState.SUCCESS
                        if HandleCombat(cached_data)
                        else BehaviorTree.NodeState.FAILURE
                    ),
                ),
            ],
        ),
    ],
)



# ---------------------------------------------------------------------------
# Keystone Mesmer Hero controller hook
# ---------------------------------------------------------------------------
# This is intentionally attached to HeroAI itself. HeroAI is already the
# confirmed per-frame runtime on the hero-owning account, so Keystone no longer
# depends on a second optional widget being discovered/enabled.
_keystone_hero_hook_logged = False
_keystone_hero_hook_last_error = ""

def _run_keystone_hero_controller() -> None:
    global _keystone_hero_hook_logged, _keystone_hero_hook_last_error

    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
    except Exception:
        CombatDebug = None

    try:
        from Py4GWCoreLib.Builds.Skills import KeystoneMesmerHeroController

        if not _keystone_hero_hook_logged:
            _keystone_hero_hook_logged = True
            if CombatDebug is not None:
                CombatDebug.log_event(
                    "HERO_KEYSTONE_HEROAI_HOOK_ACTIVE",
                    source="Widgets/Automation/Multiboxing/HeroAI.py",
                )
                CombatDebug.flush_pending(force=True)

        # Keep the controller tied to HeroAI's Combat toggle. If combat is
        # disabled, run(enabled=False) restores every native hero-AI skill flag.
        options = getattr(cached_data, "account_options", None)
        enabled = bool(options is None or getattr(options, "Combat", True))
        KeystoneMesmerHeroController.run(enabled=enabled)

    except Exception as exc:
        error_text = f"{type(exc).__name__}:{str(exc)[:220]}"
        if error_text != _keystone_hero_hook_last_error:
            _keystone_hero_hook_last_error = error_text
            if CombatDebug is not None:
                try:
                    CombatDebug.log_event(
                        "HERO_KEYSTONE_HEROAI_HOOK_ERROR",
                        error=type(exc).__name__,
                        detail=str(exc)[:220],
                    )
                    CombatDebug.flush_pending(force=True)
                except Exception:
                    pass



# ---------------------------------------------------------------------------
# RoJ Monk Hero controller hook
# ---------------------------------------------------------------------------
# RoJ heroes use the same guaranteed per-frame HeroAI runtime as Keystone.
# This removes dependence on the optional legacy "HR RoJ Hero Controller" widget.
_roj_hero_hook_logged = False
_roj_hero_hook_last_error = ""

def _run_roj_hero_controller() -> None:
    global _roj_hero_hook_logged, _roj_hero_hook_last_error
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
    except Exception:
        CombatDebug = None
    try:
        from Py4GWCoreLib.Builds.Skills import RoJMonkHeroController
        if not _roj_hero_hook_logged:
            _roj_hero_hook_logged = True
            if CombatDebug is not None:
                CombatDebug.log_event(
                    "HERO_ROJ_HEROAI_HOOK_ACTIVE",
                    source="Widgets/Automation/Multiboxing/HeroAI.py",
                )
                CombatDebug.flush_pending(force=True)
        options = getattr(cached_data, "account_options", None)
        enabled = bool(options is None or getattr(options, "Combat", True))
        RoJMonkHeroController.run(enabled=enabled)
    except Exception as exc:
        error_text = f"{type(exc).__name__}:{str(exc)[:220]}"
        if error_text != _roj_hero_hook_last_error:
            _roj_hero_hook_last_error = error_text
            if CombatDebug is not None:
                try:
                    CombatDebug.log_event(
                        "HERO_ROJ_HEROAI_HOOK_ERROR",
                        error=type(exc).__name__, detail=str(exc)[:220],
                    )
                    CombatDebug.flush_pending(force=True)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# PI / Restoration Mesmer Hero controller hook
# ---------------------------------------------------------------------------
_pi_resto_hero_hook_logged = False
_pi_resto_hero_hook_last_error = ""


def _run_pi_resto_hero_controller() -> None:
    global _pi_resto_hero_hook_logged, _pi_resto_hero_hook_last_error
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
    except Exception:
        CombatDebug = None
    try:
        from Py4GWCoreLib.Builds.Skills import PIRestorationHeroController
        if not _pi_resto_hero_hook_logged:
            _pi_resto_hero_hook_logged = True
            if CombatDebug is not None:
                CombatDebug.log_event(
                    "HERO_PI_RESTO_HEROAI_HOOK_ACTIVE",
                    source="Widgets/Automation/Multiboxing/HeroAI.py",
                )
        options = getattr(cached_data, "account_options", None)
        enabled = bool(options is None or getattr(options, "Combat", True))
        PIRestorationHeroController.run(enabled=enabled)
    except Exception as exc:
        error_text = f"{type(exc).__name__}:{str(exc)[:220]}"
        if error_text != _pi_resto_hero_hook_last_error:
            _pi_resto_hero_hook_last_error = error_text
            if CombatDebug is not None:
                try:
                    CombatDebug.log_event(
                        "HERO_PI_RESTO_HEROAI_HOOK_ERROR",
                        error=type(exc).__name__, detail=str(exc)[:220],
                    )
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Fire Elementalist Hero controller hook (PI -> Bed of Coals)
# ---------------------------------------------------------------------------
_fire_hero_hook_logged = False
_fire_hero_hook_last_error = ""

def _run_fire_hero_controller() -> None:
    global _fire_hero_hook_logged, _fire_hero_hook_last_error
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
    except Exception:
        CombatDebug = None
    try:
        from Py4GWCoreLib.Builds.Skills import FireElementalistHeroController
        if not _fire_hero_hook_logged:
            _fire_hero_hook_logged = True
            if CombatDebug is not None:
                CombatDebug.log_event(
                    "HERO_FIRE_HEROAI_HOOK_ACTIVE",
                    source="Widgets/Automation/Multiboxing/HeroAI.py",
                )
                CombatDebug.flush_pending(force=True)
        options = getattr(cached_data, "account_options", None)
        enabled = bool(options is None or getattr(options, "Combat", True))
        FireElementalistHeroController.run(enabled=enabled)
    except Exception as exc:
        error_text = f"{type(exc).__name__}:{str(exc)[:220]}"
        if error_text != _fire_hero_hook_last_error:
            _fire_hero_hook_last_error = error_text
            if CombatDebug is not None:
                try:
                    CombatDebug.log_event(
                        "HERO_FIRE_HEROAI_HOOK_ERROR",
                        error=type(exc).__name__, detail=str(exc)[:220],
                    )
                    CombatDebug.flush_pending(force=True)
                except Exception:
                    pass



# ---------------------------------------------------------------------------
# HR Mesmerway Hero Panic controller hook
# ---------------------------------------------------------------------------
_panic_hero_hook_logged = False
_panic_hero_hook_last_error = ""


def _run_panic_hero_controller() -> None:
    global _panic_hero_hook_logged, _panic_hero_hook_last_error
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
    except Exception:
        CombatDebug = None
    try:
        from Py4GWCoreLib.Builds.Skills import PanicMesmerHeroController
        if not _panic_hero_hook_logged:
            _panic_hero_hook_logged = True
            if CombatDebug is not None:
                CombatDebug.log_event(
                    "HERO_PANIC_HEROAI_HOOK_ACTIVE",
                    source="Widgets/Automation/Multiboxing/HeroAI.py",
                )
        options = getattr(cached_data, "account_options", None)
        enabled = bool(options is None or getattr(options, "Combat", True))
        PanicMesmerHeroController.run(enabled=enabled)
    except Exception as exc:
        error_text = f"{type(exc).__name__}:{str(exc)[:220]}"
        if error_text != _panic_hero_hook_last_error:
            _panic_hero_hook_last_error = error_text
            if CombatDebug is not None:
                try:
                    CombatDebug.log_event(
                        "HERO_PANIC_HEROAI_HOOK_ERROR",
                        error=type(exc).__name__, detail=str(exc)[:220],
                    )
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Urgoz Frost Ranger/Mesmer hero controller hook
# ---------------------------------------------------------------------------
_frost_ranger_hero_hook_logged = False
_frost_ranger_hero_hook_last_error = ""


def _run_frost_ranger_hero_controller() -> None:
    global _frost_ranger_hero_hook_logged, _frost_ranger_hero_hook_last_error
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
    except Exception:
        CombatDebug = None
    try:
        from Py4GWCoreLib.Builds.Skills import FrostRangerMesmerHeroController
        if not _frost_ranger_hero_hook_logged:
            _frost_ranger_hero_hook_logged = True
            if CombatDebug is not None:
                CombatDebug.log_event(
                    "HERO_FROST_RANGER_HEROAI_HOOK_ACTIVE",
                    source="Widgets/Automation/Multiboxing/HeroAI.py",
                )
        options = getattr(cached_data, "account_options", None)
        enabled = bool(options is None or getattr(options, "Combat", True))
        FrostRangerMesmerHeroController.run(enabled=enabled)
    except Exception as exc:
        error_text = f"{type(exc).__name__}:{str(exc)[:220]}"
        if error_text != _frost_ranger_hero_hook_last_error:
            _frost_ranger_hero_hook_last_error = error_text
            if CombatDebug is not None:
                try:
                    CombatDebug.log_event(
                        "HERO_FROST_RANGER_HEROAI_HOOK_ERROR",
                        error=type(exc).__name__, detail=str(exc)[:220],
                    )
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# HR Mesmerway Hero BiP controller hook
# ---------------------------------------------------------------------------
_bip_hero_hook_logged = False
_bip_hero_hook_last_error = ""


def _run_bip_hero_controller() -> None:
    global _bip_hero_hook_logged, _bip_hero_hook_last_error
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
    except Exception:
        CombatDebug = None
    try:
        from Py4GWCoreLib.Builds.Skills import BiPHeroController
        if not _bip_hero_hook_logged:
            _bip_hero_hook_logged = True
            if CombatDebug is not None:
                CombatDebug.log_event(
                    "HERO_BIP_HEROAI_HOOK_ACTIVE",
                    source="Widgets/Automation/Multiboxing/HeroAI.py",
                )
        options = getattr(cached_data, "account_options", None)
        enabled = bool(options is None or getattr(options, "Combat", True))
        BiPHeroController.run(enabled=enabled)
    except Exception as exc:
        error_text = f"{type(exc).__name__}:{str(exc)[:220]}"
        if error_text != _bip_hero_hook_last_error:
            _bip_hero_hook_last_error = error_text
            if CombatDebug is not None:
                try:
                    CombatDebug.log_event(
                        "HERO_BIP_HEROAI_HOOK_ERROR",
                        error=type(exc).__name__, detail=str(exc)[:220],
                    )
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Aura-of-the-Lich Minion Bomber hero controller hook
# ---------------------------------------------------------------------------
_minion_bomber_hero_hook_logged = False
_minion_bomber_hero_hook_last_error = ""


def _run_minion_bomber_hero_controller() -> None:
    global _minion_bomber_hero_hook_logged, _minion_bomber_hero_hook_last_error
    try:
        from Py4GWCoreLib.Builds.Skills import CombatDebug
    except Exception:
        CombatDebug = None
    try:
        from Py4GWCoreLib.Builds.Skills import MinionBomberHeroController
        if not _minion_bomber_hero_hook_logged:
            _minion_bomber_hero_hook_logged = True
            if CombatDebug is not None:
                CombatDebug.log_event(
                    "HERO_MINION_BOMBER_HEROAI_HOOK_ACTIVE",
                    source="Widgets/Automation/Multiboxing/HeroAI.py",
                )
        options = getattr(cached_data, "account_options", None)
        enabled = bool(options is None or getattr(options, "Combat", True))
        MinionBomberHeroController.run(enabled=enabled)
    except Exception as exc:
        error_text = f"{type(exc).__name__}:{str(exc)[:220]}"
        if error_text != _minion_bomber_hero_hook_last_error:
            _minion_bomber_hero_hook_last_error = error_text
            if CombatDebug is not None:
                try:
                    CombatDebug.log_event(
                        "HERO_MINION_BOMBER_HEROAI_HOOK_ERROR",
                        error=type(exc).__name__, detail=str(exc)[:220],
                    )
                except Exception:
                    pass

#region real_main
def configure():
    draw_configure_window(MODULE_NAME, HeroAI_FloatingWindows.configure_window)
    
def tooltip():
    import PyImGui
    from Py4GWCoreLib.py4gwcorelib_src.Color import Color
    from Py4GWCoreLib.ImGui import ImGui
    PyImGui.begin_tooltip()

    # Title
    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("HeroAI: Multibox Combat Engine", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()

    # Description
    PyImGui.text("An advanced multi-account synchronization and combat AI system.")
    PyImGui.text("This widget transforms extra game instances into intelligent,")
    PyImGui.text("automated party members that behave like high-performance heroes.")
    PyImGui.spacing()

    # Features
    PyImGui.text_colored("Features:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Multibox Logic: Synchronizes actions across multiple game clients")
    PyImGui.bullet_text("Advanced AI: Replaces standard hero behavior with custom combat routines")
    PyImGui.bullet_text("Intelligent interrupt logic, hex removal, enemy tracking, and more")
    PyImGui.bullet_text("Formation Control: Dynamic follower distancing and tactical positioning")
    PyImGui.bullet_text("Automation Suite: Integrated auto-looting, salvaging, and cutscene skipping")
    PyImGui.bullet_text("Behavior Trees: Complex decision-making for combat and out-of-combat states")
    PyImGui.bullet_text("Shared Memory: Seamless data exchange via the Shared Memory Manager (SMM)")

    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.spacing()

    # Credits
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Developed by Apo")
    PyImGui.bullet_text("Contributors: Mark, frenkey, Dharmantrix, aC, Greg-76, ")
    PyImGui.bullet_text("Sloppynacho, Wick-Divinus, LLYANL, Zilvereyes, valkogw")

    PyImGui.end_tooltip()

modulo = 0


def _local_party_has_heroes() -> bool:
    """Return True only when this *local player* owns at least one hero.

    Party.GetHeroes() is party-wide in multibox play, so using bool(GetHeroes())
    made every one of the 8 player clients scan the four heroes owned by the HR
    account. Hero records carry owner_player_id (login number), which lets us
    distinguish local ownership without touching hero skillbars or cached_data.
    On API uncertainty we fail open so the real hero owner never misses the
    map-entry skill lock.
    """
    try:
        own_login = int(Player.GetLoginNumber() or 0)
        heroes = list(Party.GetHeroes() or [])
        if own_login <= 0:
            return True
        return any(int(getattr(hero, "owner_player_id", -1) or -1) == own_login for hero in heroes)
    except Exception:
        return True


def _run_global_mesmer_hero_controller() -> None:
    """Drive owned eSurge/Ineptitude heroes; player builds remain unchanged."""
    global _global_mesmer_hero_error
    try:
        from Py4GWCoreLib.Builds.Skills import GlobalMesmerHeroController
        options = getattr(cached_data, "account_options", None)
        enabled = bool(options is None or getattr(options, "Combat", True))
        GlobalMesmerHeroController.run(enabled=enabled)
    except Exception as exc:
        error_text = f"{type(exc).__name__}:{str(exc)[:160]}"
        if error_text != _global_mesmer_hero_error:
            _global_mesmer_hero_error = error_text
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event('GLOBAL_MESMER_HERO_HOOK_ERROR', error=error_text)
            except Exception:
                pass

_global_mesmer_hero_error = ""



# New solo-account Keystone variant support. The original Keystone hero
# controller keeps ownership of its older, fully specified Mimicry bar.
_solo_keystone_hook_error = ""
_solo_keystone_hook_logged = False

def _run_solo_keystone_variants() -> None:
    global _solo_keystone_hook_error, _solo_keystone_hook_logged
    try:
        from Py4GWCoreLib.Builds.Skills import SoloKeystoneHeroController
        options = getattr(cached_data, "account_options", None)
        enabled = bool(options is None or getattr(options, "Combat", True))
        if not _solo_keystone_hook_logged:
            _solo_keystone_hook_logged = True
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event('SOLO_KEYSTONE_HOOK_ACTIVE', enabled=enabled,
                                      policy='local_owner_only_build_specific')
            except Exception:
                pass
        SoloKeystoneHeroController.run(enabled=enabled)
    except Exception as exc:
        error_text = f"{type(exc).__name__}:{str(exc)[:150]}"
        if error_text != _solo_keystone_hook_error:
            _solo_keystone_hook_error = error_text
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event("SOLO_KEYSTONE_HOOK_ERROR", detail=error_text)
            except Exception:
                pass

_city_bonder_hero_hook_error = ""


def _run_city_bonder_hero_controller() -> None:
    global _city_bonder_hero_hook_error
    try:
        from Py4GWCoreLib.Builds.Skills import CityBonderHeroController
        options = getattr(cached_data, "account_options", None)
        enabled = bool(options is None or getattr(options, "Combat", True))
        CityBonderHeroController.run(enabled=enabled, owner="widget")
    except Exception as exc:
        error_text = f"{type(exc).__name__}:{str(exc)[:160]}"
        if error_text != _city_bonder_hero_hook_error:
            _city_bonder_hero_hook_error = error_text
            try:
                from Py4GWCoreLib.Builds.Skills import CombatDebug
                CombatDebug.log_event("CITY_BONDER_HEROAI_HOOK_ERROR", error=error_text)
            except Exception:
                pass


def main():
    global cached_data, map_quads, modulo
    
    try:
        # Arm/lock Keystone heroes before the heavier HeroAI cache/BT update.
        # This minimizes the map-entry window in which native Hero AI could
        # fire Arcane Mimicry before the dedicated controller owns the bar.
        # Accounts without locally owned heroes do not need any of the custom
        # hero-controller discovery passes. This keeps the 8-account setup quiet
        # and avoids thousands of HERO_*_NO_MATCH diagnostics without changing
        # player-account combat logic. Hero-owning accounts still execute these
        # hooks before cached_data.Update(), preserving the map-entry AI lock.
        if _local_party_has_heroes():
            _run_keystone_hero_controller()
            _run_solo_keystone_variants()
            _run_roj_hero_controller()
            _run_pi_resto_hero_controller()
            _run_fire_hero_controller()
            _run_panic_hero_controller()
            _run_global_mesmer_hero_controller()
            _run_frost_ranger_hero_controller()
            _run_bip_hero_controller()
            _run_minion_bomber_hero_controller()
            _run_city_bonder_hero_controller()

        cached_data.Update()

        EnsureFollowModuleIni()
        HeroAI_FloatingWindows.update()
        handle_UI(cached_data)
        resurrection_scroll.tick()
        
        if initialize(cached_data):
            modulo += 1
            if modulo >= 2:
                modulo = 0
                HeroAI_BT.tick()
        else:
            map_quads.clear()
            HeroAI_BT.reset()



    except ImportError as e:
        PySystem.Console.Log(MODULE_NAME, f"ImportError encountered: {str(e)}", PySystem.Console.MessageType.Error)
        PySystem.Console.Log(MODULE_NAME, f"Stack trace: {traceback.format_exc()}", PySystem.Console.MessageType.Error)
    except ValueError as e:
        PySystem.Console.Log(MODULE_NAME, f"ValueError encountered: {str(e)}", PySystem.Console.MessageType.Error)
        PySystem.Console.Log(MODULE_NAME, f"Stack trace: {traceback.format_exc()}", PySystem.Console.MessageType.Error)
    except TypeError as e:
        PySystem.Console.Log(MODULE_NAME, f"TypeError encountered: {str(e)}", PySystem.Console.MessageType.Error)
        PySystem.Console.Log(MODULE_NAME, f"Stack trace: {traceback.format_exc()}", PySystem.Console.MessageType.Error)
    except Exception as e:
        # Catch-all for any other unexpected exceptions
        PySystem.Console.Log(MODULE_NAME, f"Unexpected error encountered: {str(e)}", PySystem.Console.MessageType.Error)
        PySystem.Console.Log(MODULE_NAME, f"Stack trace: {traceback.format_exc()}", PySystem.Console.MessageType.Error)
    finally:
        pass

def minimal():    
    draw_skip_cutscene_overlay()

def on_enable():
    heroai_build.ClearBuildContract()
    HeroAI_FloatingWindows.settings.reset()
    HeroAI_FloatingWindows.SETTINGS_THROTTLE.SetThrottleTime(50)


def on_disable():
    try:
        from Py4GWCoreLib.Builds.Skills import CityBonderHeroController
        CityBonderHeroController.reset(owner="widget")
    except Exception:
        pass


__all__ = ['main', 'configure', 'on_enable', 'on_disable']
