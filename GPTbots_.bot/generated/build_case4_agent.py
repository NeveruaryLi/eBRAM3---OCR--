"""Regenerate the Case 4 Agent I config from the user's exported baseline."""

from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build_agent_config(source_path: Path, prompt_path: Path, output_path: Path) -> Path:
    """Clone the exported FlowAgent and apply only Case 4 integration settings."""
    source_path = Path(source_path)
    prompt_path = Path(prompt_path)
    output_path = Path(output_path)

    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("Agent I prompt must not be empty")

    original = _read_json(source_path)
    if original.get("botType") != "Flow":
        raise ValueError("Case 4 source config must be a FlowAgent")

    config = copy.deepcopy(original)
    config["exportTime"] = int(time.time() * 1000)
    config["memoryEnable"] = False
    config["longTermMemory"] = False
    config["shortTermMemory"] = False
    config["toolsEnable"] = False

    components = config.get("flowRule", {}).get("components", [])
    llm_nodes = [component for component in components if component.get("type") == "LLM"]
    if len(llm_nodes) != 1:
        raise ValueError("Case 4 config must contain exactly one LLM node")

    for component in components:
        if "memoryEnable" in component:
            component["memoryEnable"] = False
        if "longTermMemory" in component:
            component["longTermMemory"] = False
        if "shortTermMemory" in component:
            component["shortTermMemory"] = False
        if "userPropertyEnable" in component:
            component["userPropertyEnable"] = False

    llm = llm_nodes[0]
    role_messages = [message for message in llm.get("messages", []) if message.get("type") == "Role"]
    if len(role_messages) != 1:
        raise ValueError("Case 4 LLM node must contain exactly one Role message")

    role_messages[0]["text"] = prompt
    llm["memoryEnable"] = False
    llm["longTermMemory"] = False
    llm["shortTermMemory"] = False
    llm["userPropertyEnable"] = False
    llm["toolsEnable"] = False
    llm["databaseEnable"] = False
    llm["workflowEnable"] = False
    llm["multiResponseTypes"] = ["Image"]
    llm["showReasoning"] = "HIDDEN"
    llm["creativityLevel"] = 0.2
    for parameter in llm.get("modelDynamicParams", []):
        if parameter.get("name") == "Temperature":
            parameter["enable"] = True
            parameter["value"] = 0.2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    base_dir = Path(__file__).resolve().parents[1]
    generated_dir = Path(__file__).resolve().parent
    parser.add_argument("--source", type=Path, default=base_dir / "Case4.bot")
    parser.add_argument(
        "--prompt",
        type=Path,
        default=generated_dir / "prompts" / "AI Model-1.md",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=generated_dir / "Case4-Agent-I.bot",
    )
    args = parser.parse_args()
    print(build_agent_config(args.source, args.prompt, args.output))


if __name__ == "__main__":
    main()
