import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "GPTbots_.bot" / "generated" / "build_case4_agent.py"
SOURCE_BOT = ROOT / "GPTbots_.bot" / "Case4.bot"


def _load_builder_module():
    spec = importlib.util.spec_from_file_location("build_case4_agent", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load builder at {BUILDER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Case4AgentBuilderTests(unittest.TestCase):
    def test_build_preserves_model_and_disables_memory(self):
        builder = _load_builder_module()
        source_before = SOURCE_BOT.read_bytes()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            prompt_path = tmp_path / "AI Model-1.md"
            output_path = tmp_path / "Case4-Agent-I.bot"
            prompt_path.write_text("Translate one legal document page image.", encoding="utf-8")

            builder.build_agent_config(SOURCE_BOT, prompt_path, output_path)

            source_after = SOURCE_BOT.read_bytes()
            generated = json.loads(output_path.read_text(encoding="utf-8"))
            source = json.loads(source_before.decode("utf-8-sig"))

        self.assertEqual(source_before, source_after)
        self.assertEqual(generated["botType"], "Flow")
        self.assertEqual(
            generated["flowRule"]["components"][2]["id"],
            source["flowRule"]["components"][2]["id"],
        )
        self.assertEqual(
            generated["flowRule"]["components"][2]["chatModelVersionId"],
            source["flowRule"]["components"][2]["chatModelVersionId"],
        )

        llm = next(c for c in generated["flowRule"]["components"] if c["type"] == "LLM")
        role = next(m for m in llm["messages"] if m["type"] == "Role")
        self.assertEqual(role["text"], "Translate one legal document page image.")
        self.assertFalse(llm["memoryEnable"])
        self.assertFalse(llm["longTermMemory"])
        self.assertFalse(llm["shortTermMemory"])
        self.assertFalse(llm["userPropertyEnable"])
        self.assertFalse(llm["toolsEnable"])
        self.assertEqual(llm["multiResponseTypes"], ["Image"])
        self.assertEqual(llm["showReasoning"], "HIDDEN")
        self.assertEqual(llm["creativityLevel"], 0.2)

    def test_build_rejects_empty_prompt(self):
        builder = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            prompt_path = tmp_path / "AI Model-1.md"
            output_path = tmp_path / "Case4-Agent-I.bot"
            prompt_path.write_text("   \n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "prompt"):
                builder.build_agent_config(SOURCE_BOT, prompt_path, output_path)


if __name__ == "__main__":
    unittest.main()
