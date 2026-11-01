from __future__ import annotations

import ast
import math
import sys
import time
import types
import unittest
from enum import IntEnum
from pathlib import Path
from typing import Any
from typing import Generator
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BUILD_PATH = ROOT / "Py4GWCoreLib" / "Builds" / "Ranger" / "R_A" / "Whirling Defense City Farmer.py"
BUILDMGR_PATH = ROOT / "Py4GWCoreLib" / "BuildMgr.py"
UTILS_PATH = ROOT / "Py4GWCoreLib" / "py4gwcorelib_src" / "Utils.py"


class Profession(IntEnum):
    _None = 0
    Ranger = 2
    Monk = 3
    Assassin = 7


class Attribute(IntEnum):
    HealingPrayers = 13
    ProtectionPrayers = 15
    DivineFavor = 16
    Expertise = 23
    WildernessSurvival = 24
    DeadlyArts = 30
    ShadowArts = 31


SKILL_NAMES = (
    "Deadly_Paradox",
    "Shadow_Form",
    "Shroud_of_Distress",
    "Unseen_Fury",
    "I_Am_Unstoppable",
    "Dwarven_Stability",
    "Whirling_Defense",
    "Throw_Dirt",
    "Quickening_Zephyr",
)
IDS = {name: index + 1 for index, name in enumerate(SKILL_NAMES)}
REQUIRED = [IDS[name] for name in SKILL_NAMES[:7]]


class _BuildMgr:
    build_name: str
    required_primary: Profession
    required_secondary: Profession
    required_skills: list[int]
    skills: list[int]
    minimum_required_match: int
    is_combat_automator_compatible: bool

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)
        self.build_name = kwargs["name"]
        self.skills = list(kwargs["required_skills"])
        self.minimum_required_match = len(self.required_skills)
        self.is_combat_automator_compatible = True


class _ChecksSkills:
    @staticmethod
    def CanCast() -> bool:
        return True


class _Effects:
    active: set[int] = set()
    remaining: dict[int, int] = {}

    @classmethod
    def HasBuff(cls, _agent_id: int, skill_id: int) -> bool:
        return skill_id in cls.active

    @classmethod
    def GetEffectTimeRemaining(cls, _agent_id: int, skill_id: int) -> int:
        return cls.remaining.get(skill_id, 0)


class _Routines:
    class Checks:
        Skills = _ChecksSkills
        Effects = _Effects


class _Player:
    @staticmethod
    def GetAgentID() -> int:
        return 101


class _Agent:
    energy: float = 0.8

    @classmethod
    def GetEnergy(cls, _agent_id: int) -> float:
        return cls.energy


class _GlobalCache:
    Effects = _Effects


class _Utils:
    @staticmethod
    def GenerateSkillbarTemplateFrom(*_args: Any) -> str:
        return "TEMPLATE"


def _load_build_class() -> Any:
    tree = ast.parse(BUILD_PATH.read_text(encoding="utf-8"))
    class_node = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Whirling_Defense_City_Farmer"
    )
    namespace: dict[str, Any] = {
        "BuildMgr": _BuildMgr,
        "HeroAI_Build": object,
        "SkillsTemplate": object,
        "Profession": Profession,
        "Utils": _Utils,
        "Attribute": Attribute,
        "Routines": _Routines,
        "Player": _Player,
        "Agent": _Agent,
        "GLOBAL_CACHE": _GlobalCache,
        "_REQUIRED_SKILLS": REQUIRED,
        "_generate_template_code": lambda: "TEMPLATE",
        **{f"{name}_ID": skill_id for name, skill_id in IDS.items()},
    }
    isolated = ast.Module(body=[class_node], type_ignores=[])
    ast.fix_missing_locations(isolated)
    exec(compile(isolated, BUILD_PATH, "exec"), namespace)
    return namespace["Whirling_Defense_City_Farmer"]


def _load_score_match() -> Any:
    tree = ast.parse(BUILDMGR_PATH.read_text(encoding="utf-8"))
    class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BuildMgr")
    function_node = next(
        node for node in class_node.body if isinstance(node, ast.FunctionDef) and node.name == "ScoreMatch"
    )
    isolated = ast.Module(body=[function_node], type_ignores=[])
    ast.fix_missing_locations(isolated)
    namespace: dict[str, Any] = {}
    exec(compile(isolated, BUILDMGR_PATH, "exec"), namespace)
    return namespace["ScoreMatch"]


def _load_utils() -> Any:
    tree = ast.parse(UTILS_PATH.read_text(encoding="utf-8"))
    class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Utils")
    isolated = ast.Module(body=[class_node], type_ignores=[])
    ast.fix_missing_locations(isolated)
    namespace: dict[str, Any] = {"math": math, "sys": sys, "time": time}
    exec(compile(isolated, UTILS_PATH, "exec"), namespace)
    return namespace["Utils"]


def _run(generator: Generator[None, None, Any]) -> Any:
    try:
        while True:
            next(generator)
    except StopIteration as stop:
        return stop.value


def _skill_method(name: str, calls: list[str], outcomes: dict[str, bool]) -> Any:
    def method(*_args: Any, **_kwargs: Any) -> Generator[None, None, bool]:
        if False:
            yield
        calls.append(name)
        return outcomes.get(name, False)

    return method


def _skill_tree(calls: list[str], outcomes: dict[str, bool]) -> Any:
    root: Any = types.SimpleNamespace()
    root.Assassin = types.SimpleNamespace()
    root.Assassin.ShadowArts = types.SimpleNamespace()
    root.Any = types.SimpleNamespace()
    root.Any.PvE = types.SimpleNamespace()
    root.Ranger = types.SimpleNamespace()
    root.Ranger.Expertise = types.SimpleNamespace()
    for name, group, method_name in (
        ("Deadly_Paradox", root.Assassin.ShadowArts, "Deadly_Paradox"),
        ("Shadow_Form", root.Assassin.ShadowArts, "Shadow_Form"),
        ("Shroud_of_Distress", root.Assassin.ShadowArts, "Shroud_of_Distress"),
        ("Unseen_Fury", root.Assassin.ShadowArts, "Unseen_Fury"),
        ("Dwarven_Stability", root.Any.PvE, "Dwarven_Stability"),
        ("I_Am_Unstoppable", root.Any.PvE, "I_Am_Unstoppable"),
        ("Whirling_Defense", root.Ranger.Expertise, "Whirling_Defense"),
        ("Throw_Dirt", root.Ranger.Expertise, "Throw_Dirt"),
    ):
        setattr(group, method_name, _skill_method(name, calls, outcomes))
    return root


WhirlingBuild = _load_build_class()
ScoreMatch = _load_score_match()


def _return_true(*_args: Any) -> bool:
    return True


def _return_false(*_args: Any) -> bool:
    return False


class WhirlingBuildMatchTests(unittest.TestCase):
    def _score(
        self,
        primary: Profession = Profession.Ranger,
        secondary: Profession = Profession.Assassin,
        skills: list[int] | None = None,
    ) -> Any:
        build = WhirlingBuild(match_only=True)
        module: Any = types.ModuleType("Py4GWCoreLib")
        module.Player = _Player
        module.Agent = _Agent
        module.Profession = Profession
        with patch.dict(sys.modules, {"Py4GWCoreLib": module}):
            return ScoreMatch(
                build,
                current_primary=primary,
                current_secondary=secondary,
                current_skills=list(REQUIRED if skills is None else skills),
            )

    def test_exact_bar_and_optional_throw_dirt_score(self):
        self.assertEqual(self._score(skills=[*REQUIRED, IDS["Throw_Dirt"]]), 8)
        self.assertEqual(self._score(), 7)

    def test_missing_required_skill_and_wrong_professions_do_not_match(self):
        self.assertEqual(self._score(skills=REQUIRED[1:]), -1)
        self.assertEqual(self._score(primary=Profession.Monk), -1)
        self.assertEqual(self._score(secondary=Profession.Monk), -1)

    def test_match_only_stays_metadata_only(self):
        build = WhirlingBuild(match_only=True)
        self.assertEqual(build.build_name, "Whirling Defense City Farmer")
        self.assertEqual(build.template_code, "TEMPLATE")
        self.assertEqual(build.skills, REQUIRED)

    def test_template_round_trip(self):
        utils = _load_utils()
        attributes = {23: 12, 31: 12, 30: 3}
        skills = [*REQUIRED, IDS["Throw_Dirt"]]
        template = utils.GenerateSkillbarTemplateFrom(2, 7, attributes, skills)
        self.assertEqual(utils.ParseSkillbarTemplate(template), (2, 7, attributes, skills))


class WhirlingBuildRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        _Effects.active = set()
        _Effects.remaining = {}
        _Agent.energy = 0.8
        self.calls: list[str] = []
        self.outcomes: dict[str, bool] = {}
        self.build = object.__new__(WhirlingBuild)
        self.build.skills = _skill_tree(self.calls, self.outcomes)
        self.build._deadly_paradox_used_for_shadow_form = False
        self.build._unseen_fury_used_for_whirling = False
        self.build.CanCastSkillID = _return_true
        self.build.IsInAggro = _return_true
        self.build.IsCloseToAggro = _return_false
        self.build.SpiritBuffExists = _return_false
        self.remaining: dict[int, int] = {
            IDS["Shadow_Form"]: 10000,
            IDS["Whirling_Defense"]: 10000,
            IDS["Unseen_Fury"]: 0,
        }
        self.build._effect_remaining_ms = self._effect_remaining

    def _effect_remaining(self, _agent_id: int, skill_id: int) -> int:
        return self.remaining.get(skill_id, 0)

    def test_shadow_form_without_qz_uses_deadly_paradox_first(self):
        self.remaining[IDS["Shadow_Form"]] = 0
        self.outcomes["Deadly_Paradox"] = True
        self.assertTrue(_run(self.build._run_local_skill_logic()))
        self.assertEqual(self.calls, ["Deadly_Paradox"])

    def test_deadly_paradox_intent_advances_to_shadow_form(self):
        self.remaining[IDS["Shadow_Form"]] = 0
        self.outcomes["Deadly_Paradox"] = True
        self.outcomes["Shadow_Form"] = True
        self.assertTrue(_run(self.build._run_local_skill_logic()))
        self.assertTrue(_run(self.build._run_local_skill_logic()))
        self.assertEqual(self.calls, ["Deadly_Paradox", "Shadow_Form"])
        self.assertFalse(self.build._deadly_paradox_used_for_shadow_form)

    def test_shadow_form_with_qz_does_not_use_deadly_paradox(self):
        self.remaining[IDS["Shadow_Form"]] = 0
        self.build.SpiritBuffExists = _return_true
        self.outcomes["Shadow_Form"] = True
        self.assertTrue(_run(self.build._run_local_skill_logic()))
        self.assertEqual(self.calls, ["Shadow_Form"])

    def test_whirling_refresh_uses_unseen_fury_as_precursor(self):
        self.remaining[IDS["Whirling_Defense"]] = 1000
        self.outcomes["Unseen_Fury"] = True
        self.assertTrue(_run(self.build._run_local_skill_logic()))
        self.assertEqual(self.calls[-1], "Unseen_Fury")
        self.assertNotIn("Deadly_Paradox", self.calls)

    def test_whirling_replaces_precursor_only_on_next_decision(self):
        self.remaining[IDS["Whirling_Defense"]] = 1000
        self.remaining[IDS["Unseen_Fury"]] = 5000
        self.outcomes["Whirling_Defense"] = True
        self.assertTrue(_run(self.build._run_local_skill_logic()))
        self.assertEqual(self.calls[-1], "Whirling_Defense")

    def test_active_whirling_is_not_replaced_and_throw_dirt_supports(self):
        self.outcomes["Throw_Dirt"] = True
        self.assertTrue(_run(self.build._run_local_skill_logic()))
        self.assertEqual(self.calls[-1], "Throw_Dirt")
        self.assertNotIn("Unseen_Fury", self.calls)


if __name__ == "__main__":
    unittest.main()
