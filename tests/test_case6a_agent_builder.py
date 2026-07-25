from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "GPTbots_.bot" / "generated" / "build_case6a_agent.py"
SOURCE_BOT = ROOT / "GPTbots_.bot" / "Case 6A.bot"


def _load_builder_module():
    spec = importlib.util.spec_from_file_location("build_case6a_agent", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load builder at {BUILDER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Case6AAgentBuilderTests(unittest.TestCase):
    def test_public_identity_does_not_expose_internal_agent_code(self):
        builder = _load_builder_module()
        source_before = SOURCE_BOT.read_bytes()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            prompt_path = tmp_path / "AI Model-1.md"
            output_path = tmp_path / "Case6A-Agent-K.bot"
            prompt_path.write_text(
                "You are the eBRAM Service Assistant. Do not provide legal advice.",
                encoding="utf-8",
            )

            builder.build_agent_config(
                SOURCE_BOT,
                prompt_path,
                output_path,
                knowledge_base_id="kb-r2",
            )
            generated = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(SOURCE_BOT.read_bytes(), source_before)
        self.assertEqual(generated["name"], "eBRAM Service Assistant")
        self.assertNotIn("Agent K", generated["introduction"])
        self.assertNotIn("Agent K", generated["firstMessage"])
        self.assertIn("How can I help", generated["firstMessage"])
        self.assertIn("請問您想了解", generated["firstMessage"])

        llm = next(
            component
            for component in generated["flowRule"]["components"]
            if component["type"] == "LLM"
        )
        role = next(message for message in llm["messages"] if message["type"] == "Role")
        self.assertNotIn("Agent K", role["text"])
        self.assertEqual(llm["shortTermMemoryRound"], 10)

        dataset = next(
            component
            for component in generated["flowRule"]["components"]
            if component["type"] == "Dataset"
        )
        self.assertEqual(dataset["docGroupIds"], ["kb-r2"])
        self.assertEqual(dataset["matchDataLimit"], 5)
        self.assertEqual(dataset["docCorrelation"], 0.76)


if __name__ == "__main__":
    unittest.main()
