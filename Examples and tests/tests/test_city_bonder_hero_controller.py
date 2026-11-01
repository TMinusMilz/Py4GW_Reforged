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

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER_PATH = ROOT / "Py4GWCoreLib" / "Builds" / "Skills" / "CityBonderHeroController.py"
UTILS_PATH = ROOT / "Py4GWCoreLib" / "py4gwcorelib_src" / "Utils.py"


class Profession(IntEnum):
    Ranger = 2
    Monk = 3


SKILL_NAMES = (
    "Healing_Breeze",
    "Rebirth",
    "Quickening_Zephyr",
    "Blessed_Signet",
    "Mending",
    "Balthazars_Spirit",
    "Life_Bond",
    "Life_Barrier",
)
IDS = {name: index + 1 for index, name in enumerate(SKILL_NAMES)}


def _load_utils() -> Any:
    tree = ast.parse(UTILS_PATH.read_text(encoding="utf-8"))
    class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Utils")
    isolated = ast.Module(body=[class_node], type_ignores=[])
    ast.fix_missing_locations(isolated)
    namespace: dict[str, Any] = {"math": math, "sys": sys, "time": time}
    exec(compile(isolated, UTILS_PATH, "exec"), namespace)
    return namespace["Utils"]


def _return_true(*_args: Any) -> bool:
    return True


def _return_false(*_args: Any) -> bool:
    return False


def _return_zero(*_args: Any) -> int:
    return 0


def _return_empty_list(*_args: Any) -> list[Any]:
    return []


def _skill_id(name: str) -> int:
    return IDS[name]


def _energy_cost(_skill_id: int) -> int:
    return 5


def _zero_distance(_first: Any, _second: Any) -> float:
    return 0.0


def _load_controller() -> types.ModuleType:
    tree = ast.parse(CONTROLLER_PATH.read_text(encoding="utf-8"))
    body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    isolated = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(isolated)

    skill = types.SimpleNamespace(
        GetID=_skill_id,
        Data=types.SimpleNamespace(GetEnergyCost=_energy_cost),
    )
    namespace: dict[str, Any] = {
        "dataclass": __import__("dataclasses").dataclass,
        "field": __import__("dataclasses").field,
        "Agent": types.SimpleNamespace(),
        "AgentArray": types.SimpleNamespace(),
        "GLOBAL_CACHE": types.SimpleNamespace(),
        "Map": types.SimpleNamespace(),
        "Party": types.SimpleNamespace(Heroes=types.SimpleNamespace()),
        "Player": types.SimpleNamespace(),
        "Profession": Profession,
        "Range": types.SimpleNamespace(
            Spellcast=types.SimpleNamespace(value=1000.0),
            Earshot=types.SimpleNamespace(value=1000.0),
        ),
        "Routines": types.SimpleNamespace(Checks=types.SimpleNamespace(Agents=types.SimpleNamespace())),
        "Skill": skill,
        "SkillBar": types.SimpleNamespace(),
        "Utils": types.SimpleNamespace(Distance=_zero_distance),
        "Attribute": IntEnum(
            "Attribute", {"ProtectionPrayers": 15, "HealingPrayers": 13, "DivineFavor": 16, "WildernessSurvival": 24}
        ),
    }
    module = types.ModuleType("CityBonderHeroControllerUnderTest")
    module.__dict__.update(namespace)
    sys.modules[module.__name__] = module
    exec(compile(isolated, CONTROLLER_PATH, "exec"), module.__dict__)
    return module


class CityBonderTemplateTests(unittest.TestCase):
    def test_bonder_template_round_trip(self):
        utils = _load_utils()
        attributes = {15: 12, 13: 10, 16: 4, 24: 7}
        skills = list(IDS.values())
        template = utils.GenerateSkillbarTemplateFrom(3, 2, attributes, skills)
        self.assertEqual(utils.ParseSkillbarTemplate(template), (3, 2, attributes, skills))


class CityBonderControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller: Any = _load_controller()
        self.controller.reset()
        self.now = 10000
        self.controller._now = self._now
        self.bar: list[Any] = [
            types.SimpleNamespace(id=types.SimpleNamespace(id=skill_id), recharge=0) for skill_id in IDS.values()
        ]
        self.heroes: list[Any] = [types.SimpleNamespace(agent_id=501, owner_player_id=77)]
        self.effects: set[int] = set()
        self.cast_calls: list[tuple[int, int, int, int]] = []
        self.ai_calls: list[tuple[int, int, bool]] = []
        self.player_dead = False
        self.hero_energy = 1.0

        self.controller.SkillBar.GetHeroSkillbar = self._get_bar
        self.controller.SkillBar.HeroUseSkill = self._use_skill
        self.controller.Party.GetHeroes = self._get_heroes
        self.controller.Party.IsPartyLoaded = _return_true
        self.controller.Party.Heroes.SetSkillAIEnabled = self._set_ai
        self.controller.Player.GetLoginNumber = self._login_number
        self.controller.Player.GetAgentID = self._player_agent_id
        self.controller.Agent.GetProfessions = self._get_professions
        self.controller.Agent.IsDead = self._is_dead
        self.controller.Agent.IsAlive = self._is_alive
        self.controller.Agent.IsKnockedDown = _return_false
        self.controller.Agent.IsCasting = _return_false
        self.controller.Agent.IsMoving = _return_false
        self.controller.Agent.GetCastingSkillID = _return_zero
        self.controller.Agent.GetEnergy = self._get_energy
        self.controller.Agent.GetMaxEnergy = self._max_energy
        self.controller.Agent.GetXY = self._get_xy
        self.controller.Agent.GetPlayerNumber = _return_zero
        self.controller.AgentArray.GetSpiritPetArray = _return_empty_list
        self.controller.Routines.Checks.Agents.HasEffect = self._has_effect
        self.controller.Map.GetMapID = self._map_id
        self.controller.Map.IsExplorable = _return_true

    def tearDown(self) -> None:
        self.controller.reset()

    def _now(self) -> int:
        return self.now

    def _get_bar(self, _index: int) -> list[Any]:
        return list(self.bar)

    def _get_heroes(self) -> list[Any]:
        return list(self.heroes)

    @staticmethod
    def _login_number() -> int:
        return 77

    @staticmethod
    def _player_agent_id() -> int:
        return 101

    def _get_professions(self, _hero_id: int) -> tuple[Profession, Profession]:
        return Profession.Monk, Profession.Ranger

    def _is_dead(self, agent_id: int) -> bool:
        return self.player_dead if agent_id == 101 else False

    def _is_alive(self, agent_id: int) -> bool:
        return not self.player_dead if agent_id == 101 else True

    def _get_energy(self, _agent_id: int) -> float:
        return self.hero_energy

    @staticmethod
    def _max_energy(_agent_id: int) -> int:
        return 100

    @staticmethod
    def _get_xy(_agent_id: int) -> tuple[float, float]:
        return 0.0, 0.0

    def _has_effect(self, _agent_id: int, skill_id: int) -> bool:
        return skill_id in self.effects

    @staticmethod
    def _map_id() -> int:
        return 123

    def _use_skill(self, target_id: int, slot: int, hero_index: int) -> None:
        self.cast_calls.append((self.bar[slot - 1].id.id, target_id, slot, hero_index))

    def _set_ai(self, hero_id: int, slot: int, enabled: bool) -> None:
        self.ai_calls.append((hero_id, slot, enabled))

    def _confirm_last_cast(self) -> None:
        if not self.cast_calls:
            return
        skill_id = self.cast_calls[-1][0]
        self.effects.add(skill_id)
        state = self.controller._STATES[501]
        state.pending = self.controller._Pending()
        self.now += 200

    def test_exact_local_hero_matches_and_first_bond_precedes_qz(self):
        self.controller.run(enabled=True, owner="widget")
        self.assertIn(501, self.controller._STATES)
        self.assertEqual(self.cast_calls[0][0], IDS["Life_Barrier"])
        self.assertNotIn(IDS["Quickening_Zephyr"], [call[0] for call in self.cast_calls])
        self.assertEqual({call[1] for call in self.ai_calls if not call[2]}, set(range(1, 9)))

    def test_non_local_hero_is_not_controlled(self):
        self.heroes[0].owner_player_id = 999
        self.controller.run(enabled=True, owner="widget")
        self.assertEqual(self.controller._matching(), [])
        self.assertEqual(self.cast_calls, [])
        self.assertEqual(self.ai_calls, [])

    def test_wrong_hero_bar_is_not_controlled(self):
        self.bar[0].id.id = 99
        self.controller.run(enabled=True, owner="widget")
        self.assertEqual(self.controller._matching(), [])
        self.assertEqual(self.cast_calls, [])

    def test_all_bonds_are_established_before_quickening_zephyr(self):
        expected = [
            IDS[name] for name in ("Life_Barrier", "Life_Bond", "Mending", "Balthazars_Spirit", "Quickening_Zephyr")
        ]
        for expected_skill_id in expected:
            self.controller.run(enabled=True, owner="widget")
            self.assertEqual(self.cast_calls[-1][0], expected_skill_id)
            self._confirm_last_cast()

    def test_rebirth_runs_while_player_is_dead(self):
        self.player_dead = True
        self.controller.run(enabled=True, owner="widget")
        self.assertEqual(self.cast_calls[0][0], IDS["Rebirth"])
        self.assertEqual(self.cast_calls[0][1], 101)

    def test_energy_gate_prevents_unaffordable_cast(self):
        self.hero_energy = 0.0
        self.controller.run(enabled=True, owner="widget")
        self.assertEqual(self.cast_calls, [])

    def test_disable_restores_native_ai_and_releases_owner(self):
        self.controller.run(enabled=True, owner="widget")
        self.controller.run(enabled=False, owner="widget")
        self.assertEqual({call[1] for call in self.ai_calls if call[2]}, set(range(1, 9)))
        self.assertEqual(self.controller._active_owner, "")

    def test_second_runtime_cannot_take_ownership(self):
        self.controller.run(enabled=True, owner="widget")
        cast_count = len(self.cast_calls)
        self.controller.run(enabled=True, owner="headless")
        self.assertEqual(self.controller._active_owner, "widget")
        self.assertEqual(len(self.cast_calls), cast_count)


if __name__ == "__main__":
    unittest.main()
