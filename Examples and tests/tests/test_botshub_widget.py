from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
import unittest
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[2]
WIDGET_DIR = ROOT / "Widgets" / "Automation" / "Bots"
WIDGET_PATH = WIDGET_DIR / "BotsHub.py"


@dataclass
class FakeWidget:
    folder_script_name: str
    widget_path: str
    name: str
    plain_name: str = ""
    enabled: bool = False
    category: str = "Automation"
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    has_configure_property: bool = False


class FakeCatalogQuery:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class FakeWidgetCatalog:
    @staticmethod
    def query(snapshot, query):
        return [
            widget
            for widget in snapshot.widgets_by_id.values()
            if widget.widget_path == query.path or widget.widget_path.startswith(f"{query.path}/")
        ]


class RecordingHandler:
    def __init__(self, widget: FakeWidget) -> None:
        self.widget = widget
        self.calls: list[tuple[str, str]] = []

    def enable_widget(self, widget_id: str) -> None:
        self.calls.append(("enable", widget_id))
        self.widget.enabled = True

    def disable_widget(self, widget_id: str) -> None:
        self.calls.append(("disable", widget_id))
        self.widget.enabled = False


class BotsHubWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module_names = (
            "PyImGui",
            "PySystem",
            "Py4GWCoreLib",
            "Py4GWCoreLib.ImGui",
            "Py4GWCoreLib.py4gwcorelib_src",
            "Py4GWCoreLib.py4gwcorelib_src.Settings",
            "Py4GWCoreLib.py4gwcorelib_src.WidgetManager",
        )
        cls.saved_modules = {name: sys.modules.get(name) for name in cls.module_names}

        for name in cls.module_names:
            sys.modules[name] = types.ModuleType(name)

        console = types.SimpleNamespace(
            MessageType=types.SimpleNamespace(Info=1, Error=2),
            Log=lambda *args: None,
        )
        setattr(sys.modules["PySystem"], "Console", console)
        setattr(sys.modules["Py4GWCoreLib.ImGui"], "ImGui", object)
        setattr(sys.modules["Py4GWCoreLib.py4gwcorelib_src.Settings"], "Settings", object)

        widget_manager = sys.modules["Py4GWCoreLib.py4gwcorelib_src.WidgetManager"]
        setattr(widget_manager, "CatalogScope", str)
        setattr(widget_manager, "Widget", FakeWidget)
        setattr(widget_manager, "WidgetCatalog", FakeWidgetCatalog)
        setattr(widget_manager, "WidgetCatalogQuery", FakeCatalogQuery)
        setattr(widget_manager, "WidgetCatalogSnapshot", object)
        setattr(widget_manager, "WidgetHandler", object)
        setattr(widget_manager, "get_widget_handler", lambda: None)

        spec = importlib.util.spec_from_file_location("botshub_widget_test_target", WIDGET_PATH)
        assert spec is not None and spec.loader is not None
        cls.hub = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.hub
        spec.loader.exec_module(cls.hub)

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop("botshub_widget_test_target", None)
        for name, module in cls.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self) -> None:
        self.hub._state.group = self.hub.ALL_GROUPS
        self.hub._state.scope = "all"
        self.hub._state.search_text = ""
        self.snapshot = types.SimpleNamespace(
            widgets_by_id={
                self.hub.HUB_WIDGET_ID: FakeWidget(self.hub.HUB_WIDGET_ID, self.hub.BOTS_ROOT, "BotsHub"),
                "Automation/Bots/Farmers/A.py": FakeWidget(
                    "Automation/Bots/Farmers/A.py",
                    "Automation/Bots/Farmers",
                    "A",
                ),
                "Automation/Bots/Missions/B.py": FakeWidget(
                    "Automation/Bots/Missions/B.py",
                    "Automation/Bots/Missions",
                    "B",
                ),
                "Automation/Helpers/C.py": FakeWidget(
                    "Automation/Helpers/C.py",
                    "Automation/Helpers",
                    "C",
                ),
            }
        )

    def test_widget_folder_has_discovery_marker(self) -> None:
        self.assertTrue((WIDGET_DIR / ".widget").is_file(), "BotsHub needs a .widget marker for discovery")

    def test_groups_come_only_from_bot_folders(self) -> None:
        self.assertEqual(["Farmers", "Missions"], self.hub._bot_groups(self.snapshot))

    def test_all_bots_excludes_hub_and_non_bot_widgets(self) -> None:
        observed = self.hub._query_bots(self.snapshot)
        self.assertEqual(["A", "B"], [widget.name for widget in observed])

    def test_group_filter_uses_full_catalog_path(self) -> None:
        self.hub._state.group = "Farmers"
        observed = self.hub._query_bots(self.snapshot)
        self.assertEqual(["A"], [widget.name for widget in observed])

    def test_control_actions_use_stable_full_widget_id(self) -> None:
        widget = self.snapshot.widgets_by_id["Automation/Bots/Farmers/A.py"]
        handler = RecordingHandler(widget)
        setattr(self.hub, "_widget_handler", handler)

        self.hub._set_widget_open(widget, True)
        self.hub._set_widget_open(widget, False)

        self.assertEqual(
            [("enable", "Automation/Bots/Farmers/A.py"), ("disable", "Automation/Bots/Farmers/A.py")],
            handler.calls,
        )


if __name__ == "__main__":
    unittest.main()
