from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "GPTbots_.bot" / "generated" / "build_case6b_agent.py"
SOURCE_BOT = ROOT / "GPTbots_.bot" / "EBRAM-Case6B.bot"
CASE6B_DIR = ROOT / "GPTbots_.bot" / "generated" / "case6b"


def _load_builder_module():
    spec = importlib.util.spec_from_file_location("build_case6b_agent", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load builder at {BUILDER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Case6BAgentBuilderTests(unittest.TestCase):
    def test_build_preserves_flow_identity_and_configures_two_json_stages(self):
        builder = _load_builder_module()
        source_before = SOURCE_BOT.read_bytes()
        source = json.loads(SOURCE_BOT.read_text(encoding="utf-8-sig"))
        source_models = {
            component["id"]: component.get("chatModelVersionId")
            for component in source["flowRule"]["components"]
            if component["type"] == "LLM"
        }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "Case6B-Agent-L.bot"
            builder.build_agent_config(
                SOURCE_BOT,
                CASE6B_DIR / "prompts",
                output,
            )
            generated = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(SOURCE_BOT.read_bytes(), source_before)
        self.assertEqual(generated["name"], "Agent L - Service Agreement Drafting")
        self.assertEqual(generated["botType"], "Flow")
        self.assertFalse(generated["longTermMemory"])
        self.assertTrue(generated["shortTermMemory"])
        self.assertFalse(generated["toolsEnable"])
        self.assertFalse(generated["userPropertyEnable"])
        self.assertEqual(generated["showReasoning"], "HIDDEN")

        components = {
            component["id"]: component
            for component in generated["flowRule"]["components"]
        }
        self.assertEqual(
            {component_id: (component["type"], component["name"]) for component_id, component in components.items()},
            {
                1: ("Input", "User Input"),
                2: ("Output", "Output"),
                3: ("Regular", "Regular-1"),
                4: ("LLM", "AI Model-1"),
                6: ("LLM", "AI Model-2"),
            },
        )

        rule = components[3]["regularGroups"][0]["items"][0]
        self.assertEqual(
            (rule["propertyKey"], rule["op"], rule["value"]),
            ("sys_user_msg_count", "eq", "1"),
        )

        for component_id, phase in (
            (4, "[CASE6B_PHASE:TEMPLATE_PARSE]"),
            (6, "[CASE6B_PHASE:FIELD_FILL]"),
        ):
            component = components[component_id]
            role = next(message for message in component["messages"] if message["type"] == "Role")
            self.assertIn(phase, role["text"])
            self.assertEqual(component["chatModelVersionId"], source_models[component_id])
            self.assertEqual(component["responseFormat"], "JsonObject")
            self.assertEqual(component["creativityLevel"], 0.1)
            self.assertFalse(component["longTermMemory"])
            self.assertTrue(component["shortTermMemory"])
            self.assertFalse(component["userPropertyEnable"])
            self.assertFalse(component["toolsEnable"])
            self.assertFalse(component["workflowEnable"])
            self.assertFalse(component["databaseEnable"])
            self.assertEqual(component["showReasoning"], "HIDDEN")

        self.assertEqual(
            components[4]["multiModalLlmInput"],
            {"fileMode": "SYSTEM", "fileSupportTypes": ["Document"]},
        )
        self.assertEqual(
            components[6]["multiModalLlmInput"],
            {"fileMode": "DISABLED", "fileSupportTypes": None},
        )

    def test_case6b_fixtures_cover_template_and_all_nine_materials(self):
        manifest = json.loads(
            (CASE6B_DIR / "fixtures" / "template_field_manifest.json").read_text(encoding="utf-8")
        )
        summary = json.loads(
            (CASE6B_DIR / "fixtures" / "full_summary.json").read_text(encoding="utf-8")
        )
        expected = json.loads(
            (CASE6B_DIR / "fixtures" / "expected_fill.json").read_text(encoding="utf-8")
        )

        fields = manifest["fields"]
        self.assertEqual(len(fields), 37)
        self.assertEqual(len({field["field_id"] for field in fields}), 37)
        self.assertEqual(
            sum(field["field_kind"] in {"repeatable_service", "repeatable_price"} for field in fields),
            20,
        )
        self.assertEqual(
            sum(field["field_kind"].startswith("signature") for field in fields),
            6,
        )

        sources = summary["sources"]
        self.assertEqual(len(sources), 9)
        self.assertEqual(len({source["filename"] for source in sources}), 9)

        expected_fields = expected["fields"]
        self.assertEqual(len(expected_fields), 37)
        self.assertEqual(
            {field["field_id"] for field in expected_fields},
            {field["field_id"] for field in fields},
        )
        self.assertEqual(
            sum(field["status"] == "REMOVE" for field in expected_fields),
            12,
        )
        self.assertEqual(
            sum(field["status"] == "LEAVE_BLANK" for field in expected_fields),
            6,
        )


if __name__ == "__main__":
    unittest.main()
