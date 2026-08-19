"""Boundaries and packaging invariants for what S-Initiative ships.

The pre-split repository checked every distribution at once, from paths no
published repository has, so none of this shipped. These are source scans
rather than integration tests, so S-Initiative can hold its own share and fail
in the pull request that breaks it. Core and S-Cockpit hold theirs.
"""

import ast
import importlib.metadata
import re
import unittest
from importlib.resources import files
from pathlib import Path

import s_initiative
import sovereign


ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted((ROOT / "src").rglob("*.py"))
OTHER_APPLICATIONS = ("s_cockpit", "s_team")


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
        self.assertEqual(importlib.metadata.version("sovereign-initiative"), s_initiative.__version__)

    def test_installed_browser_assets_are_available(self):
        self.assertIn(
            "<!doctype html",
            files("s_initiative.assets").joinpath("initiative.html").read_text(
                encoding="utf-8",
            ),
        )
        self.assertTrue(files("s_initiative.assets").joinpath("initiative.css").is_file())

    def test_package_sources_live_under_the_declared_src_root(self):
        # Asserting where the imported module loaded from only holds for an
        # editable install: CI installs a wheel, so __file__ points into
        # site-packages. The invariant is this repository's layout - the
        # source sits under src/, and no flat copy survives beside it for an
        # import to pick up ahead of the installed package.
        self.assertTrue((ROOT / "src" / "s_initiative" / "__init__.py").is_file())
        self.assertFalse((ROOT / "s_initiative").exists())

    def test_executable_spec_collects_current_application_packages(self):
        # Read the collect_all list rather than matching quoted text, so the
        # test does not depend on which quote style the spec happens to use.
        source = (ROOT / "S-Initiative.spec").read_text(encoding="utf-8")
        collected = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.For) and isinstance(node.iter, (ast.Tuple, ast.List)):
                collected.update(
                    element.value for element in node.iter.elts
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                )
        self.assertTrue(collected, "no collect_all package list found in the spec")
        self.assertLessEqual({"sovereign", "s_initiative"}, collected)
        # Modules from before the package split; naming one would freeze a
        # build that silently omits the code it was meant to bundle.
        self.assertTrue(collected.isdisjoint(
            {"kanban_logic", "relay_logic", "boardofboards_logic"},
        ))
        # This spec builds S-Initiative's own executable, so it collects this
        # application and the Core it runs on, and nothing else. The reason
        # is scope, not licensing: every application is Apache-2.0, so a
        # combined binary crosses no licence boundary that Core's LGPL has
        # not already set. S-Cockpit owns the spec that bundles all
        # of them, because the Cockpit is what such a binary opens.
        self.assertTrue(collected.isdisjoint(set(OTHER_APPLICATIONS)))


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCES, "no S-Initiative sources found")

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

    def test_does_not_read_mutable_session_registries(self):
        forbidden = {
            "peer_topic_sets", "peer_perspectives", "peer_identity_key",
            "active_topic_uuids", "app_metadata",
        }
        for path in SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            used = {
                node.attr for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
            }
            self.assertFalse(used & forbidden, str(path))

    def test_reads_the_transition_ranking_rather_than_copying_it(self):
        # S-Initiative and Agreement had each copied Session's ranking and the
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
        path = ROOT / "src" / "s_initiative" / "logic.py"
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

    def test_board_get_uses_the_composite_snapshot_boundary(self):
        source = (
            ROOT / "src" / "s_initiative" / "controller.py"
        ).read_text(encoding="utf-8")
        self.assertIn("runtime.composite_response(", source)
        self.assertIn("logic.board_snapshot", source)
        self.assertIn("logic.merge_board_observation", source)


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.initiative = files("s_initiative.assets").joinpath("initiative.html").read_text(
            encoding="utf-8",
        )
        self.css = files("s_initiative.assets").joinpath("initiative.css").read_text(
            encoding="utf-8",
        )

    # The two faces ------------------------------------------------------

    def _function_body(self, name: str) -> str:
        after = self.initiative.split(f"function {name}(", 1)[1]
        return after.split("\n      function ", 1)[0]

    def test_the_mandate_face_rebuilds_and_the_board_face_patches(self):
        # DESIGN_INITIATIVE_UI.md 8. reconcileDOM needs a create/update pair
        # per node type; the mandate face will have seven, and a pair
        # falling out of step draws the old thing until something forces a
        # rebuild - a silent bug class this codebase has already met. The
        # board keeps the helper because drag-and-drop needs stable element
        # identity across renders.
        self.assertNotIn("reconcileDOM", self._function_body("renderMandate"))
        self.assertIn("replaceChildren", self._function_body("renderMandate"))
        self.assertIn("reconcileDOM", self._function_body("renderBoard"))

    def test_the_mandate_face_does_not_rebuild_under_the_caret(self):
        # The 1500ms poll empties the box mid-word otherwise.
        body = self._function_body("renderMandate")
        self.assertIn("document.activeElement", body)
        self.assertIn("isContentEditable", body)

    def test_the_mandate_face_skips_an_unchanged_payload_but_not_on_revision(self):
        body = self._function_body("renderMandate")
        self.assertIn("renderedMandateJson", body)
        # Peer liveness is merged into the snapshot after it is read, so a
        # transport change may not advance `revision` and the face would
        # freeze. The JSON string is the honest comparison.
        self.assertNotIn("revision", body)

    def test_needs_use_the_shared_composer_reorder_and_editor(self):
        region = self._function_body("needsRegion")
        self.assertIn("SovereignUI.reorderableList", region)
        row = self._function_body("needRow")
        self.assertIn("SovereignUI.reorderHandle", row)
        self.assertIn("SovereignUI.editableText", row)
        self.assertIn("SovereignUI.addComposer", self._function_body("needComposer"))

    def test_a_need_carries_a_divergence_lamp(self):
        # Content gets a lamp and a reaction control; the absence of one on a
        # record is what tells the two apart, so this half has to be present.
        row = self._function_body("needRow")
        self.assertIn("reactionTools(need)", row)
        self.assertIn("applyTransitionClass", row)

    def test_the_beneficiary_label_is_drawn_beside_the_live_name_not_replaced(self):
        # S-Team's rule inverted: the beneficiary of a need is very often not
        # an actor in this system at all, so the label is the primary fact.
        # Where the uuid does resolve, both are drawn - the two disagreeing is
        # information, and hiding one throws it away.
        line = self._function_body("beneficiaryLine")
        self.assertIn("beneficiary_label", line)
        self.assertIn("actorName(need.data.beneficiary_actor_uuid)", line)
        self.assertIn("line.append(label)", line)
        self.assertIn("known here as", line)

    def test_the_second_face_is_not_named_after_the_whole_topic(self):
        # The topic is the initiative. A face called Initiative would give one
        # word to a part and to the whole, which is the stutter 9 avoids
        # elsewhere - and it is why the face's own identifiers say mandate
        # while `state.initiative` stays what the face is showing.
        self.assertIn('["mandate", "Mandate"]', self.initiative)
        self.assertNotIn('"Initiative"]', self.initiative)

    def test_the_face_switch_is_in_the_content_area_and_not_the_bar(self):
        # U7: the bar's navigation row is destinations among topics, and a
        # row where some items change topic and some change view teaches
        # nothing about either. `setAppActions` is gone besides.
        self.assertNotIn("setAppActions", self.initiative)
        main = self.initiative.split("<main>", 1)[1].split("</main>", 1)[0]
        self.assertIn('id="faceSwitch"', main)
        self.assertIn('id="board"', main)
        self.assertIn('id="mandate"', main)

    def test_opening_lands_on_the_board_and_the_face_is_never_remembered(self):
        # A remembered face means one link opens two different pages for two
        # people, and the board is where the work is.
        self.assertIn('face: "board"', self.initiative)
        self.assertNotIn("localStorage", self.initiative)
        self.assertNotIn("sessionStorage", self.initiative)
        # And no second route parameter: `?topic=` is the only one Core
        # composes routes from.
        self.assertNotIn("?face=", self.initiative)
        self.assertNotIn('params.get("face")', self.initiative)

    def test_the_faces_are_hidden_by_a_rule_that_outranks_their_layout(self):
        # .board is display:flex, so [hidden] has to be said louder.
        self.assertIn(".board[hidden]", self.css)
        self.assertIn(".mandate-face[hidden]", self.css)

    def test_the_objective_is_edited_on_the_face_with_the_shared_editor(self):
        head = self._function_body("mandateHead")
        self.assertIn("SovereignUI.editableText", head)
        self.assertIn("/api/initiative/initiatives/set_objective", head)

    def test_a_claimed_date_is_said_and_never_offered_for_correction(self):
        # Claiming happens on the day it is true, from the board. Clearing
        # one is undoing a claim rather than tidying a field, so the
        # mandate face must not draw it as another box to correct.
        dates = self._function_body("mandateDates")
        self.assertIn("actual_start", dates)
        self.assertNotIn("claim_date", dates)
        planned = self._function_body("plannedDate")
        self.assertIn("/api/initiative/initiatives/set_dates", planned)
        self.assertNotIn("actual_", planned)

    def test_topic_header_delegates_navigation_and_creation_to_the_shell(self):
        self.assertNotIn("onCreateTopic", self.initiative)
        self.assertIn("SovereignShell.setTopicName", self.initiative)
        # No list of this client's other initiatives in this application's bar.
        # Reaching another initiative is the Cockpit's.
        self.assertNotIn("initiatives.map", self.initiative)
        self.assertNotIn("/api/initiative/initiatives/select", self.initiative.split(
            "async function selectInitiativeFromUrl", 1,
        )[0])

    def test_the_page_only_reads_payload_keys_the_logic_writes(self):
        # DESIGN_INITIATIVE_UI.md 8: the payload keys are a contract with the
        # page and are not the node types. S-Team renamed both together once,
        # in lockstep with its tests, and nothing failed while the page read
        # `undefined` and drew an empty document beside a team that was there
        # the whole time. Asserting it from the page's side is what makes half
        # a rename fail here rather than in front of somebody.
        source = (ROOT / "src" / "s_initiative" / "logic.py").read_text(
            encoding="utf-8",
        )
        written = set()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name != "board_payload":
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Dict):
                    written |= {
                        key.value for key in inner.value.keys
                        if isinstance(key, ast.Constant)
                    }
        self.assertIn("initiative", written)
        read = set(re.findall(r"state\??\.([a-z_]+)", self.initiative))
        self.assertTrue(read)
        self.assertEqual(read - written, set())

    def test_people_and_add_actions_use_the_shared_ui_primitives(self):
        self.assertIn("SovereignUI.avatar", self.initiative)
        self.assertIn('add.textContent = "+ Add card"', self.initiative)
        self.assertIn('addBtn.textContent = "+ Add column"', self.initiative)

    def test_column_and_agenda_text_use_the_shared_editor(self):
        self.assertIn("SovereignUI.editableText", self.initiative)
        self.assertIn('className = "column-name"', self.initiative)
        self.assertNotIn('document.createElement("input")', self.initiative.split(
            "function renderColumn", 1,
        )[1].split("function renderCard", 1)[0])
        self.assertIn("update: '/api/initiative/agenda/update'", self.initiative)

    def test_columns_use_the_shared_horizontal_reorder_control(self):
        self.assertIn("SovereignUI.reorderHandle", self.initiative)
        self.assertIn("SovereignUI.reorderableList", self.initiative)
        self.assertIn('axis: "horizontal"', self.initiative)
        self.assertNotIn("draggedColumn", self.initiative)
        self.assertNotIn("async function dropColumn", self.initiative)

    def test_assets_never_navigate_to_the_bare_root_with_a_query(self):
        # "/" serves whichever application is primary, so a root-relative link
        # lands somewhere that depends on host configuration. Cross-application
        # navigation must name the target's asset prefix.
        for number, line in enumerate(self.initiative.splitlines(), start=1):
            for pattern in ('href = `/?', 'href="/?', "href='/?"):
                self.assertNotIn(pattern, line, f"initiative.html:{number}")

    def test_card_drop_always_clears_drag_styling(self):
        drop = self.initiative.split(
            "async function commitCardDrop", 1,
        )[1].split("function onCardDragOver", 1)[0]
        self.assertIn('querySelector(".card.dragging")', drop)
        self.assertIn('classList.remove("dragging")', drop)

    def test_card_drag_has_preview_and_suppresses_text_selection(self):
        card = self.initiative.split(
            "function renderCard", 1,
        )[1].split("function isInteractiveCardTarget", 1)[0]
        self.assertIn("event.preventDefault()", card)
        self.assertIn('preview.classList.add("card-drag-preview")', card)
        self.assertIn('document.body.append(preview)', card)
        self.assertIn(".card-drag-pending *", self.css)
        self.assertIn("user-select: none", self.css)
        self.assertIn(".card.card-drag-preview", self.css)

    def test_card_hover_uses_a_theme_token(self):
        hover = self.css.split(".card:hover", 1)[1].split("}", 1)[0]
        light = self.css.split(':root[data-theme="light"]', 1)[1].split("}", 1)[0]
        self.assertIn("background: var(--card-hover)", hover)
        self.assertIn("--card-hover:", light)
        self.assertNotIn("#30312f", hover)


if __name__ == "__main__":
    unittest.main()
