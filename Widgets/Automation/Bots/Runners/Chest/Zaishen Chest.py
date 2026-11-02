from typing import Any
from typing import Generator

import PyImGui

from Py4GWCoreLib import ActionQueueManager
from Py4GWCoreLib import Agent
from Py4GWCoreLib import AgentArray
from Py4GWCoreLib import Console
from Py4GWCoreLib import ConsoleLog
from Py4GWCoreLib import GLOBAL_CACHE
from Py4GWCoreLib import Item
from Py4GWCoreLib import ItemType
from Py4GWCoreLib import Listeners
from Py4GWCoreLib import Map
from Py4GWCoreLib import MerchantWindow
from Py4GWCoreLib import ModelID
from Py4GWCoreLib import Player
from Py4GWCoreLib import Range
from Py4GWCoreLib import Routines
from Py4GWCoreLib import SalvageMode
from Py4GWCoreLib.py4gwcorelib_src.system_settings.loot_filters import LootFilters
from Py4GWCoreLib.routines_src.BehaviourTrees import BT
from Py4GWCoreLib.routines_src.yield_src.helpers import _run_bt_tree

MODULE_NAME = "Zaishen Chest"
MODULE_ICON = "Assets\\Textures\\Module_Icons\\ZaishenBounty.png"

_ISLE_OF_THE_NAMELESS = 280
_CHEST_SEARCH_DISTANCE = 2500
_USE_KEY_DIALOG_ID = 2
_ZAISHEN_CHEST_GADGET_ID = 9523
_LOW_REQUIREMENT_MAX = 8
_DESIRABLE_SKINS = {
    "Amethyst Aegis",
    "Celestial Compass",
    "Chaos Axe",
    "Crystalline Sword",
    "Draconic Aegis",
    "Dragon's Breath Wand",
    "Eternal Blade",
    "Eternal Bow",
    "Eternal Shield",
    "Fellblade",
    "Ghostly Staff",
    "Gothic Dual Axe",
    "Katana",
    "Obsidian Edge",
    "Pyroclastic Axe",
    "Shinobi Blade",
    "Storm Bow",
    "Stygian Reaver",
    "Voltaic Spear",
}
_VALUABLE_UPGRADES = {
    "AptitudeNotAttitude",
    "ForgetMeNot",
    "LikeARollingStone",
    "LiveForToday",
    "OfTheProfession",
}

running: bool = False
routine_active: bool = False
stop_after_one: bool = False
status: str = "Ready"
starting_key_count: int = 0
kept_skin_count: int = 0
salvaged_mod_count: int = 0
sold_item_count: int = 0


def _stop(message: str) -> None:
    global running, status

    running = False
    status = message
    ActionQueueManager().ResetAllQueues()
    ConsoleLog(MODULE_NAME, message, Console.MessageType.Info)


def _start(open_once: bool = False) -> None:
    global running, routine_active, stop_after_one, status, starting_key_count
    global kept_skin_count, salvaged_mod_count, sold_item_count

    if running or routine_active:
        return

    starting_key_count = GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Zaishen_Key)
    kept_skin_count = 0
    salvaged_mod_count = 0
    sold_item_count = 0
    stop_after_one = open_once
    status = "Starting..."
    routine_active = True
    running = True
    GLOBAL_CACHE.Coroutines.append(_run())


def _get_nearest_zaishen_chest() -> int:
    nearby_gadgets = AgentArray.Filter.ByDistance(
        AgentArray.GetGadgetArray(),
        Player.GetXY(),
        _CHEST_SEARCH_DISTANCE,
    )
    nearby_gadgets = AgentArray.Sort.ByDistance(nearby_gadgets, Player.GetXY())
    for agent_id in nearby_gadgets:
        if Agent.GetGadgetID(agent_id) == _ZAISHEN_CHEST_GADGET_ID:
            return agent_id
    return 0


def _auto_key_listener_active() -> bool:
    try:
        chest_listener = Listeners.AutoOpenLockedChest
        return bool(chest_listener.IsEnabled() and chest_listener.GetUseKey())
    except Exception:
        return False


def _open_chest(chest_id: int) -> Generator[Any, Any, bool]:
    keys_before = GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Zaishen_Key)
    chest_x, chest_y = Agent.GetXY(chest_id)
    reached_chest = yield from Routines.Yield.Movement.FollowPath(
        [(chest_x, chest_y)],
        custom_exit_condition=lambda: not running,
    )
    if not reached_chest or not running:
        return False

    try:
        yield from Routines.Yield.Player.InteractAgent(chest_id)
    except Exception as exc:
        # The native interact can report "cannot reach chest or item" even when
        # the game already queued the interaction; let the key/drop checks below
        # decide whether the chest really opened.
        ConsoleLog(MODULE_NAME, f"Chest interact reported an error: {exc}", Console.MessageType.Warning)
    yield from Routines.Yield.wait(500)
    if not running:
        return False

    if not _auto_key_listener_active():
        Player.SendDialog(_USE_KEY_DIALOG_ID)
    for _ in range(60):
        if GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Zaishen_Key) < keys_before:
            return True
        if _chest_drop_appeared():
            return True
        if not running:
            return False
        yield from Routines.Yield.wait(100)

    ConsoleLog(MODULE_NAME, "The chest did not consume a key.", Console.MessageType.Warning)
    return False


def _chest_drop_appeared() -> bool:
    """A nearby drop we may take means the chest actually opened."""
    try:
        player_agent_id = Player.GetAgentID()
        nearby_items = AgentArray.Filter.ByDistance(
            AgentArray.GetItemArray(),
            Player.GetXY(),
            Range.Area.value,
        )
        for item_id in nearby_items:
            if Agent.GetItemAgentOwnerID(item_id) in (0, player_agent_id):
                return True
    except Exception:
        pass
    return False


def _collect_drops() -> Generator[Any, Any, bool]:
    """Pick up the chest drops without aborting the run on unreachable items.

    Walking to a drop's exact coordinates and calling InteractAgent directly
    fails when the drop landed where the pathfinder only gets within its
    tolerance (the chest alcove): the native layer rejects the interact with
    "cannot reach chest or item". Target + InteractTarget lets the game client
    walk the remaining gap itself, and per-item failures are skipped instead
    of stopping the whole run.
    """
    picked_up: set[int] = set()
    rounds_without_progress = 0

    for _ in range(2):
        loot_array = LootFilters().GetLootArray(Range.Area.value)
        if not loot_array:
            break

        attempted = False
        for item_id in loot_array:
            if not running:
                return False
            if item_id in picked_up or item_id not in AgentArray.GetItemArray():
                continue
            attempted = True

            try:
                yield from Routines.Yield.Player.ChangeTarget(item_id)
                yield from Routines.Yield.wait(250)
                if Player.GetTargetID() != item_id:
                    continue
                yield from Routines.Yield.Player.InteractTarget()
            except Exception as exc:
                ConsoleLog(
                    MODULE_NAME,
                    f"Skipping item {item_id} after an interact error: {exc}",
                    Console.MessageType.Warning,
                )
                continue

            for _ in range(30):
                if item_id not in AgentArray.GetItemArray():
                    picked_up.add(item_id)
                    break
                if not running:
                    return False
                yield from Routines.Yield.wait(100)

        if not attempted:
            break
        if picked_up:
            rounds_without_progress = 0
        else:
            rounds_without_progress += 1

    if rounds_without_progress >= 2:
        ConsoleLog(
            MODULE_NAME,
            "No drops could be picked up; the remaining items were left on the ground.",
            Console.MessageType.Warning,
        )
    return running


def _is_low_req_desirable_skin(item_id: int) -> bool:
    if Item.GetName(item_id) not in _DESIRABLE_SKINS:
        return False

    _, requirement = Item.Properties.GetRequirement(item_id)
    return requirement <= _LOW_REQUIREMENT_MAX


def _get_valuable_upgrades(item_id: int) -> list[tuple[str, int]]:
    valuable_upgrades: list[tuple[str, int]] = []
    item_type = Item.GetItemType(item_id)[0]

    for name, slot in Item.Mods.GetUpgrades(item_id):
        is_shield_devotion = name == "OfDevotion" and item_type == ItemType.Shield
        if name not in _VALUABLE_UPGRADES and not is_shield_devotion:
            continue
        if not Item.Mods.IsMaxed(item_id, name):
            continue
        valuable_upgrades.append((name, int(slot)))

    return valuable_upgrades


def _get_salvage_mode(slot: int) -> SalvageMode | None:
    if slot == int(Item.Mods.Slot.Prefix):
        return SalvageMode.Prefix
    if slot == int(Item.Mods.Slot.Suffix):
        return SalvageMode.Suffix
    if slot == int(Item.Mods.Slot.Inscription):
        return SalvageMode.Inscription
    return None


def _salvage_valuable_mods(item_id: int) -> Generator[Any, Any, bool]:
    global status, salvaged_mod_count

    while running:
        valuable_upgrades = _get_valuable_upgrades(item_id)
        if not valuable_upgrades:
            return True
        if GLOBAL_CACHE.Inventory.GetFreeSlotCount() < 1:
            return False

        name, slot = valuable_upgrades[0]
        salvage_mode = _get_salvage_mode(slot)
        if salvage_mode is None:
            return False

        status = f"Salvaging {name}..."
        tree = BT.Items.SalvageItem(
            item_id=item_id,
            salvage_mode=salvage_mode,
            timeout_ms_per_item=5000,
        )
        salvaged = yield from _run_bt_tree(tree, return_bool=True, throttle_ms=100)
        if not salvaged:
            return False
        salvaged_mod_count += 1

    return False


def _sell_items(item_ids: list[int]) -> Generator[Any, Any, bool]:
    if not item_ids:
        return True

    merchant_id = yield from Routines.Yield.Agents.GetAgentIDByName("[Merchant]")
    if not merchant_id:
        ConsoleLog(MODULE_NAME, "No [Merchant] agent found.", Console.MessageType.Warning)
        return False
    if not running:
        return False

    merchant_x, merchant_y = Agent.GetXY(merchant_id)
    reached_merchant = yield from Routines.Yield.Movement.FollowPath(
        [(merchant_x, merchant_y)],
        custom_exit_condition=lambda: not running,
    )
    if not reached_merchant or not running:
        return False

    yield from Routines.Yield.Player.InteractAgent(merchant_id)
    for _ in range(30):
        if not running:
            return False
        if MerchantWindow.IsOpen():
            break
        yield from Routines.Yield.wait(100)
    else:
        ConsoleLog(MODULE_NAME, "The merchant window did not open.", Console.MessageType.Warning)
        return False

    yield from Routines.Yield.Merchant.SellItems(item_ids)
    remaining_ids = item_ids.copy()
    for _ in range(50):
        if not running:
            return False
        inventory_ids = set(GLOBAL_CACHE.Inventory.GetAllInventoryItemIds())
        remaining_ids = [item_id for item_id in item_ids if item_id in inventory_ids]
        if not remaining_ids:
            if MerchantWindow.IsOpen():
                MerchantWindow.Close()
                yield from Routines.Yield.wait(250)
            return True
        yield from Routines.Yield.wait(100)

    remaining_names = ", ".join(Item.GetName(item_id) for item_id in remaining_ids)
    ConsoleLog(MODULE_NAME, f"Items left after the merchant timeout: {remaining_names}", Console.MessageType.Warning)
    return False


def _process_new_golds(item_ids: set[int]) -> Generator[Any, Any, bool]:
    global status, kept_skin_count, sold_item_count

    if not running:
        return False

    gold_ids = [item_id for item_id in item_ids if Item.Rarity.IsGold(item_id)]
    if not gold_ids:
        return True

    unidentified_ids = [
        item_id for item_id in gold_ids
        if not Item.item_instance(item_id).is_identified
    ]
    if unidentified_ids:
        if GLOBAL_CACHE.Inventory.GetFirstIDKit() == 0:
            _stop("Stopped: no identification kit for the new gold items.")
            return False

        status = f"Identifying {len(unidentified_ids)} gold item(s)..."
        yield from Routines.Yield.Items.IdentifyItemsAndVerify(unidentified_ids)
        if any(not Item.item_instance(item_id).is_identified for item_id in unidentified_ids):
            _stop("Stopped: could not identify every new gold item.")
            return False

    items_to_sell: list[int] = []
    for item_id in gold_ids:
        if not running:
            return False

        if _is_low_req_desirable_skin(item_id):
            kept_skin_count += 1
            status = f"Keeping low-requirement {Item.GetName(item_id)}."
            continue

        salvaged = yield from _salvage_valuable_mods(item_id)
        if not salvaged:
            _stop(f"Stopped: could not salvage valuable mods from {Item.GetName(item_id)}.")
            return False
        items_to_sell.append(item_id)

    if items_to_sell:
        status = f"Selling {len(items_to_sell)} non-valuable gold item(s)..."
        sold = yield from _sell_items(items_to_sell)
        if not sold:
            if running:
                _stop("Stopped: merchant could not sell every remaining item.")
            return False
        sold_item_count += len(items_to_sell)

    return running


def _run() -> Generator[Any, Any, None]:
    global running, routine_active, status

    try:
        while running:
            if not Routines.Checks.Map.MapValid():
                status = "Waiting for the map to load..."
                yield from Routines.Yield.wait(500)
                continue

            if Map.GetMapID() != _ISLE_OF_THE_NAMELESS:
                _stop("Stopped: travel to the Isle of the Nameless.")
                return

            if GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Zaishen_Key) < 1:
                _stop("Finished: no Zaishen Keys left.")
                return

            if GLOBAL_CACHE.Inventory.GetFreeSlotCount() < 1:
                _stop("Stopped: inventory is full.")
                return

            chest_id = _get_nearest_zaishen_chest()
            if chest_id == 0:
                _stop("Stopped: no Zaishen Chest found nearby.")
                return

            inventory_before = set(GLOBAL_CACHE.Inventory.GetAllInventoryItemIds())
            status = "Opening the Zaishen Chest..."
            opened = yield from _open_chest(chest_id)
            if not opened:
                if running:
                    _stop("Stopped: could not reach or open the chest.")
                return

            status = "Collecting the drops..."
            if not (yield from _collect_drops()):
                return

            inventory_after = set(GLOBAL_CACHE.Inventory.GetAllInventoryItemIds())
            processed = yield from _process_new_golds(inventory_after - inventory_before)
            if not processed:
                return
            if stop_after_one:
                _stop("Finished: one test chest complete.")
                return

            status = "Ready for the next key..."
            yield from Routines.Yield.wait(250)
    except Exception as exc:
        ConsoleLog(MODULE_NAME, f"Chest routine failed: {exc}", Console.MessageType.Error)
        _stop(f"Stopped after an error: {exc}")
    finally:
        running = False
        routine_active = False


def main() -> None:
    if not PyImGui.begin(MODULE_NAME, PyImGui.WindowFlags.AlwaysAutoResize):
        return

    try:
        key_count = GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Zaishen_Key)
        free_slots = GLOBAL_CACHE.Inventory.GetFreeSlotCount()
        opened_count = max(starting_key_count - key_count, 0) if starting_key_count else 0

        PyImGui.text(f"Zaishen Keys: {key_count}")
        PyImGui.text(f"Free inventory slots: {free_slots}")
        PyImGui.text(f"Chests opened this session: {opened_count}")
        PyImGui.text(f"Low-req skins kept: {kept_skin_count}")
        PyImGui.text(f"Valuable mods salvaged: {salvaged_mod_count}")
        PyImGui.text(f"Junk golds sold: {sold_item_count}")
        PyImGui.separator()
        PyImGui.text(f"Status: {status}")

        if running:
            if PyImGui.button("Stop"):
                _stop("Stopped by user.")
        elif routine_active:
            PyImGui.text_disabled("Stopping...")
        else:
            if PyImGui.button("Open Once"):
                _start(open_once=True)
            if PyImGui.button("Start"):
                _start()

        PyImGui.separator()
        PyImGui.text_disabled("Start on the Isle of the Nameless, near the chest.")
        PyImGui.text_disabled("Bring an ID kit and an expert salvage kit.")
        PyImGui.text_disabled("Keeps listed skins at req 8 or lower; salvages max rare mods.")
        PyImGui.text_disabled("Only processes new loot. Drops use your current loot filters.")
    except Exception as exc:
        ConsoleLog(MODULE_NAME, f"UI draw failed: {exc}", Console.MessageType.Error)
    finally:
        PyImGui.end()


def on_disable() -> None:
    if running:
        _stop("Stopped: widget disabled.")


if __name__ == "__main__":
    main()
