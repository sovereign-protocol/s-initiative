"""Boundaries and packaging invariants for what S-Kanban ships.

The pre-split repository checked every distribution at once, from paths no
published repository has, so none of this shipped. These are source scans
rather than integration tests, so S-Kanban can hold its own share and fail
in the pull request that breaks it. Core and Personal Cockpit hold theirs.
"""

import ast
import importlib.metadata
import unittest
from importlib.resources import files
from pathlib import Path

import s_kanban
import sovereign


ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted((ROOT / "src").rglob("*.py"))
OTHER_APPLICATIONS = ("personal_cockpit", "s_agreement")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }


class PackagingTests(unittest.TestCase):
    def test_distribution_and_module_versions_agree(self):
        # The pre-split test hard-coded both numbers and went stale on every
        # release. Agreement between metadata and module is the invariant.
        self.assertEqual(importlib.metadata.version("s-kanban"), s_kanban.__version__)

    def test_installed_browser_assets_are_available(self):
        self.assertIn(
            "<!doctype html",
            files("s_kanban.assets").joinpath("kanban.html").read_text(
                encoding="utf-8",
            ),
        )
        self.assertTrue(files("s_kanban.assets").joinpath("kanban.css").is_file())

    def test_package_sources_live_under_the_declared_src_root(self):
        # Asserting where the imported module loaded from only holds for an
        # editable install: CI installs a wheel, so __file__ points into
        # site-packages. The invariant is this repository's layout - the
        # source sits under src/, and no flat copy survives beside it for an
        # import to pick up ahead of the installed package.
        self.assertTrue((ROOT / "src" / "s_kanban" / "__init__.py").is_file())
        self.assertFalse((ROOT / "s_kanban").exists())

    def test_executable_spec_collects_current_application_packages(self):
        # Read the collect_all list rather than matching quoted text, so the
        # test does not depend on which quote style the spec happens to use.
        source = (ROOT / "S-Kanban.spec").read_text(encoding="utf-8")
        collected = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.For) and isinstance(node.iter, (ast.Tuple, ast.List)):
                collected.update(
                    element.value for element in node.iter.elts
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                )
        self.assertTrue(collected, "no collect_all package list found in the spec")
        self.assertLessEqual({"sovereign", "s_kanban"}, collected)
        # Modules from before the package split; naming one would freeze a
        # build that silently omits the code it was meant to bundle.
        self.assertTrue(collected.isdisjoint(
            {"kanban_logic", "relay_logic", "boardofboards_logic"},
        ))
        # Applications from the other repositories cross a licence boundary.
        self.assertTrue(collected.isdisjoint(set(OTHER_APPLICATIONS)))


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCES, "no S-Kanban sources found")

    def test_imports_core_only_through_its_public_root(self):
        public_names = set(sovereign.__all__)
        for path in SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            violations = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    violations.extend(
                        alias.name for alias in node.names
                        if alias.name == "sovereign"
                        or alias.name.startswith("sovereign.")
                    )
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.startswith("sovereign."):
                        violations.append(module)
                    elif module == "sovereign":
                        violations.extend(
                            f"sovereign.{alias.name}"
                            for alias in node.names
                            if alias.name == "*" or alias.name not in public_names
                        )
            self.assertEqual(violations, [], str(path))

    def test_does_not_import_another_application(self):
        for path in SOURCES:
            imports = imported_modules(path)
            self.assertFalse(any(
                name == package or name.startswith(f"{package}.")
                for name in imports
                for package in OTHER_APPLICATIONS
            ), str(path))

    def test_does_not_read_private_channel_services_from_config(self):
        for path in SOURCES:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn('config.get("_channel_manager")', source, str(path))
            self.assertNotIn('config.get("_relay_manager")', source, str(path))
            self.assertNotIn("channel_manager", source, str(path))

    def test_reads_the_transition_ranking_rather_than_copying_it(self):
        # Kanban and Agreement had each copied Session's ranking and the
        # copies drifted: one ranked divergence 6, the other 5, so the same
        # conflict surfaced differently in each. Session owns the ranking.
        for path in SOURCES:
            source = path.read_text(encoding="utf-8")
            if "TRANSITION_PRIORITY" not in source:
                continue
            self.assertIn("Session.TRANSITION_PRIORITY", source, str(path))
            for literal in ('"divergence": 5', '"divergence": 6'):
                self.assertNotIn(literal, source, f"{path} re-declares the ranking")

    def test_domain_logic_does_not_depend_on_host_or_http_controllers(self):
        path = ROOT / "src" / "s_kanban" / "logic.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = imported_modules(path)
        self.assertFalse(
            any(
                name == "starlette"
                or name.startswith("starlette.")
                or name.endswith(".controller")
                or name.endswith("_controller")
                or name == "sovereign.application"
                for name in imports
            ),
            str(path),
        )
        self.assertFalse(any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"build_routes", "create_application"}
            for node in tree.body
        ), str(path))
        self.assertFalse(any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(arg.arg == "runtime" for arg in node.args.args)
            for node in ast.walk(tree)
        ), str(path))


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.kanban = files("s_kanban.assets").joinpath("kanban.html").read_text(
            encoding="utf-8",
        )

    def test_topic_header_delegates_navigation_and_creation_to_the_shell(self):
        self.assertNotIn("onCreateTopic", self.kanban)
        self.assertIn("SovereignShell.setTopicSelector", self.kanban)

    def test_assets_never_navigate_to_the_bare_root_with_a_query(self):
        # "/" serves whichever application is primary, so a root-relative link
        # lands somewhere that depends on host configuration. Cross-application
        # navigation must name the target's asset prefix.
        for number, line in enumerate(self.kanban.splitlines(), start=1):
            for pattern in ('href = `/?', 'href="/?', "href='/?"):
                self.assertNotIn(pattern, line, f"kanban.html:{number}")


if __name__ == "__main__":
    unittest.main()
