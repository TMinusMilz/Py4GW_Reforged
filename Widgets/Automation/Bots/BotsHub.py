from __future__ import annotations

from dataclasses import dataclass

import PyImGui
import PySystem

from Py4GWCoreLib.ImGui import ImGui
from Py4GWCoreLib.py4gwcorelib_src.Settings import Settings
from Py4GWCoreLib.py4gwcorelib_src.WidgetManager import CatalogScope
from Py4GWCoreLib.py4gwcorelib_src.WidgetManager import Widget
from Py4GWCoreLib.py4gwcorelib_src.WidgetManager import WidgetCatalog
from Py4GWCoreLib.py4gwcorelib_src.WidgetManager import WidgetCatalogQuery
from Py4GWCoreLib.py4gwcorelib_src.WidgetManager import WidgetCatalogSnapshot
from Py4GWCoreLib.py4gwcorelib_src.WidgetManager import WidgetHandler
from Py4GWCoreLib.py4gwcorelib_src.WidgetManager import get_widget_handler

MODULE_NAME = "BotsHub"
MODULE_CATEGORY = "Automation"
MODULE_TAGS = ["Automation", "Bots", "Hub"]
MODULE_ICON = "Assets/Textures/Module_Icons/Template.png"
MODULE_DESCRIPTION = "Find, open, configure, and close the bots installed in Widgets/Automation/Bots."
OPTIONAL = True

BOTS_ROOT = "Automation/Bots"
HUB_WIDGET_ID = f"{BOTS_ROOT}/BotsHub.py"
INI_PATH = "Widgets/BotsHub"
INI_FILENAME = "BotsHub.ini"
ALL_GROUPS = "All groups"
SCOPE_LABELS = ("All bots", "Open", "Closed")
SCOPE_VALUES: tuple[CatalogScope, ...] = ("all", "active", "inactive")


@dataclass
class BotsHubState:
    search_text: str = ""
    group: str = ALL_GROUPS
    scope: CatalogScope = "all"
    selected_widget_id: str = ""
    status_message: str = "Select a bot to open its controls."
    initialized: bool = False


_state = BotsHubState()
_widget_handler: WidgetHandler = get_widget_handler()
_ini_key = ""


def _log(message: str, error: bool = False) -> None:
    message_type = PySystem.Console.MessageType.Error if error else PySystem.Console.MessageType.Info
    PySystem.Console.Log(MODULE_NAME, message, message_type)


def _save_settings() -> None:
    if not _ini_key:
        return
    settings = Settings(_ini_key, "account")
    settings.set("Browser", "group", _state.group)
    settings.set("Browser", "scope", _state.scope)
    settings.set("Browser", "selected_widget_id", _state.selected_widget_id)


def _load_settings() -> None:
    global _ini_key
    if _state.initialized:
        return

    _ini_key = Settings(f"{INI_PATH}/{INI_FILENAME}", "account").name
    if not _ini_key:
        _state.status_message = "Settings are unavailable; bot controls remain usable for this session."
        _log(_state.status_message, error=True)
        _state.initialized = True
        return

    settings = Settings(_ini_key, "account")
    _state.group = settings.get_str("Browser", "group", ALL_GROUPS)
    scope = settings.get_str("Browser", "scope", "all")
    _state.scope = scope if scope in SCOPE_VALUES else "all"
    _state.selected_widget_id = settings.get_str("Browser", "selected_widget_id", "")
    _state.initialized = True


def _select_widget(widget_id: str) -> None:
    _state.selected_widget_id = widget_id
    _save_settings()


def _is_bot_widget(widget: Widget) -> bool:
    if widget.folder_script_name == HUB_WIDGET_ID:
        return False
    path = widget.widget_path.replace("\\", "/")
    return path == BOTS_ROOT or path.startswith(f"{BOTS_ROOT}/")


def _bot_groups(snapshot: WidgetCatalogSnapshot) -> list[str]:
    groups: set[str] = set()
    for widget in snapshot.widgets_by_id.values():
        if not _is_bot_widget(widget):
            continue
        path = widget.widget_path.replace("\\", "/")
        if path == BOTS_ROOT:
            continue
        relative_path = path[len(BOTS_ROOT) + 1 :]
        if relative_path:
            groups.add(relative_path.split("/", 1)[0])
    return sorted(groups, key=str.lower)


def _query_bots(snapshot: WidgetCatalogSnapshot) -> list[Widget]:
    path = BOTS_ROOT if _state.group == ALL_GROUPS else f"{BOTS_ROOT}/{_state.group}"
    query = WidgetCatalogQuery(
        text=_state.search_text,
        path=path,
        scope=_state.scope,
        sort_by="name",
    )
    return [widget for widget in WidgetCatalog.query(snapshot, query) if widget.folder_script_name != HUB_WIDGET_ID]


def _set_widget_open(widget: Widget, open_controls: bool) -> None:
    try:
        if open_controls:
            _widget_handler.enable_widget(widget.folder_script_name)
        else:
            _widget_handler.disable_widget(widget.folder_script_name)
    except Exception as exc:
        _state.status_message = f"Could not change {widget.name}: {exc}"
        _log(_state.status_message, error=True)
        return

    if widget.enabled != open_controls:
        _state.status_message = f"WidgetManager did not change the state of {widget.name}."
        _log(_state.status_message, error=True)
        return

    if open_controls:
        _state.status_message = f"Opened {widget.name}. Use its own Start button to begin farming."
    else:
        _state.status_message = f"Closed {widget.name}."
    _log(_state.status_message)


def _configure_widget(widget: Widget) -> None:
    try:
        _widget_handler.set_widget_configuring(widget.folder_script_name, True)
        _state.status_message = f"Opened configuration for {widget.name}."
    except Exception as exc:
        _state.status_message = f"Could not configure {widget.name}: {exc}"
        _log(_state.status_message, error=True)


def _draw_filters(snapshot: WidgetCatalogSnapshot) -> None:
    PyImGui.set_next_item_width(280.0)
    _state.search_text = PyImGui.input_text_with_hint(
        "##bots_hub_search",
        "Search bots; separate terms with semicolons",
        _state.search_text,
        0,
    )

    groups = [ALL_GROUPS, *_bot_groups(snapshot)]
    if _state.group not in groups:
        _state.group = ALL_GROUPS
    group_index = PyImGui.combo("Group##bots_hub", groups.index(_state.group), groups)
    if group_index != groups.index(_state.group):
        _state.group = groups[group_index]
        _save_settings()

    scope_index = SCOPE_VALUES.index(_state.scope)
    new_scope_index = PyImGui.combo("Status##bots_hub", scope_index, list(SCOPE_LABELS))
    if new_scope_index != scope_index:
        _state.scope = SCOPE_VALUES[new_scope_index]
        _save_settings()


def _draw_bot_row(widget: Widget) -> None:
    status = "Open" if widget.enabled else "Closed"
    label = f"{widget.name}##{widget.folder_script_name}"
    if PyImGui.selectable(label, widget.folder_script_name == _state.selected_widget_id):
        _select_widget(widget.folder_script_name)
    if PyImGui.is_item_hovered():
        PyImGui.set_tooltip(f"{widget.widget_path}\n{status}")


def _draw_bot_list(bots: list[Widget]) -> None:
    if not PyImGui.begin_child("##bots_hub_list", (330.0, -56.0), 1, 0):
        PyImGui.end_child()
        return

    if not bots:
        PyImGui.text_disabled("No bots match these filters.")
    for widget in bots:
        _draw_bot_row(widget)
    PyImGui.end_child()


def _draw_selected_bot(snapshot: WidgetCatalogSnapshot) -> None:
    if not PyImGui.begin_child("##bots_hub_details", (0.0, -56.0), 1, 0):
        PyImGui.end_child()
        return

    widget = snapshot.widgets_by_id.get(_state.selected_widget_id)
    if widget is None or not _is_bot_widget(widget):
        PyImGui.text_disabled("Select a bot from the list.")
        PyImGui.end_child()
        return

    PyImGui.text(widget.name)
    PyImGui.text_disabled(widget.widget_path)
    PyImGui.text(f"Controls: {'Open' if widget.enabled else 'Closed'}")
    PyImGui.spacing()

    if widget.enabled:
        if PyImGui.button(f"Close controls##{widget.folder_script_name}"):
            _set_widget_open(widget, False)
    elif PyImGui.button(f"Open controls##{widget.folder_script_name}"):
        _set_widget_open(widget, True)

    if widget.has_configure_property:
        if PyImGui.button(f"Configure##{widget.folder_script_name}"):
            _configure_widget(widget)

    PyImGui.separator()
    PyImGui.text_wrapped(
        "Opening a bot shows its interface and enables its callbacks. Use the bot's own Start button to begin farming."
    )
    PyImGui.end_child()


def _draw_window() -> None:
    PyImGui.set_next_window_size((780.0, 500.0), PyImGui.ImGuiCond.FirstUseEver)
    if not ImGui.Begin(_ini_key, MODULE_NAME):
        return

    snapshot = WidgetCatalog.snapshot_from_handler(_widget_handler)
    open_bot_count = sum(1 for widget in snapshot.widgets_by_id.values() if widget.enabled and _is_bot_widget(widget))
    installed_bot_count = sum(1 for widget in snapshot.widgets_by_id.values() if _is_bot_widget(widget))
    PyImGui.text(f"Installed bots: {installed_bot_count}")
    PyImGui.same_line(0.0, 16.0)
    PyImGui.text(f"Open: {open_bot_count}")
    _draw_filters(snapshot)
    bots = _query_bots(snapshot)
    PyImGui.text_disabled(f"{len(bots)} matching")
    PyImGui.separator()
    _draw_bot_list(bots)
    PyImGui.same_line(0.0, 8.0)
    _draw_selected_bot(snapshot)
    PyImGui.separator()
    PyImGui.text_wrapped(_state.status_message)
    ImGui.End(_ini_key)


def tooltip() -> None:
    if not PyImGui.begin_tooltip():
        return
    PyImGui.text(MODULE_DESCRIPTION)
    PyImGui.text_wrapped("Bot-specific farm setup, inventory handling, and Start/Stop controls remain in each bot.")
    PyImGui.end_tooltip()


def main() -> None:
    _load_settings()
    _draw_window()
