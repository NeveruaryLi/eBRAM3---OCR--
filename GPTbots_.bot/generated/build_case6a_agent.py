"""Regenerate the Case 6A Agent K config from the exported FlowAgent baseline."""

from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path


EXPECTED_FLOW = {
    1: ("Input", "User Input"),
    2: ("Output", "Output"),
    3: ("Dataset", "KGs-1"),
    4: ("LLM", "AI Model-1"),
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _set_temperature(config: dict, value: float) -> None:
    config["creativityLevel"] = value
    params = config.setdefault("modelDynamicParams", [])
    temperature = next((item for item in params if item.get("name") == "Temperature"), None)
    if temperature is None:
        params.append({"name": "Temperature", "enable": True, "value": value})
    else:
        temperature.update({"enable": True, "value": value})


def _assert_baseline(config: dict) -> dict[int, dict]:
    if config.get("botType") != "Flow":
        raise ValueError("Case 6A source config must be a FlowAgent")
    components = config.get("flowRule", {}).get("components", [])
    by_id = {component.get("id"): component for component in components}
    if set(by_id) != set(EXPECTED_FLOW):
        raise ValueError("Case 6A source must contain exactly the four expected nodes")
    for component_id, (component_type, component_name) in EXPECTED_FLOW.items():
        component = by_id[component_id]
        if (component.get("type"), component.get("name")) != (component_type, component_name):
            raise ValueError(
                f"Unexpected component {component_id}: "
                f"{component.get('type')!r}/{component.get('name')!r}"
            )
    return by_id


def build_agent_config(
    source_path: Path,
    prompt_path: Path,
    output_path: Path,
    knowledge_base_id: str | None = None,
) -> Path:
    """Clone the exported FlowAgent and apply only the approved Agent K settings."""
    prompt = Path(prompt_path).read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("Agent K prompt must not be empty")

    config = copy.deepcopy(_read_json(Path(source_path)))
    components = _assert_baseline(config)
    dataset = components[3]
    llm = components[4]

    config.update(
        {
            "exportTime": int(time.time() * 1000),
            "name": "eBRAM Service Assistant",
            "introduction": (
                "Website service guidance based on official eBRAM sources, "
                "available in English and Traditional Chinese."
            ),
            "maxRespTokens": 1200,
            "reasoningEffort": "MINIMAL",
            "showReasoning": "HIDDEN",
            "reasoningEnabled": False,
            "dataEnable": True,
            "embeddingRate": 0.9,
            "docCorrelation": 0.76,
            "docCorrelationSwitch": True,
            "matchDataLimit": 5,
            "rerankSwitch": True,
            "memoryEnable": True,
            "longTermMemory": False,
            "shortTermMemory": True,
            "shortTermMemoryRound": 10,
            "userPropertyEnable": False,
            "toolsEnable": False,
            "showTools": False,
            "workflowEnable": False,
            "showWorkflow": False,
            "associatedWorkflows": [],
            "databaseEnable": False,
            "multiResponseTypes": ["Text"],
            "responseFormat": "Text",
            "nextQuestion": False,
            "firstMessage": (
                "Hello! How can I help you with eBRAM’s services, rules, fees or "
                "platform support?\n\n"
                "您好！請問您想了解 eBRAM 的服務、規則、費用或平台支援嗎？"
            ),
        }
    )
    _set_temperature(config, 0.2)

    multimodal = config.setdefault("multiModal", {}).setdefault("multiModalInput", {})
    multimodal.update(
        {
            "fileLimit": 1,
            "fileMode": "DISABLED",
            "fileSupportTypes": None,
            "fileSwitch": False,
            "textSwitch": True,
        }
    )
    config.setdefault("multiModal", {}).setdefault("multiModalOutput", {})["textSwitch"] = True

    for component in components.values():
        if "longTermMemory" in component:
            component["longTermMemory"] = False
        if "shortTermMemory" in component:
            component["shortTermMemory"] = component.get("type") == "LLM"
        if "userPropertyEnable" in component:
            component["userPropertyEnable"] = False
        if "toolsEnable" in component:
            component["toolsEnable"] = False
        if "workflowEnable" in component:
            component["workflowEnable"] = False
        if "databaseEnable" in component:
            component["databaseEnable"] = False
        component["associatedWorkflows"] = []
        component["databaseTableIds"] = []
        component["keyEventConfig"] = None

    dataset.update(
        {
            "docGroupIds": [knowledge_base_id] if knowledge_base_id else [],
            "docCorrelation": 0.76,
            "matchDataLimit": 5,
            "embeddingRate": 0.9,
            "rerankSwitch": True,
            "rerankModelVersionId": config.get("rerankModelVersionId") or "",
            "customKnowledgeType": "DEFAULT",
            "dataSourceShowType": "LIST_SHOW",
            "enhancementMessageSwitch": True,
            "graphSwitch": False,
            "metadataFilter": [],
            "metadataFilterLogic": "AND",
        }
    )

    role_messages = [message for message in llm.get("messages", []) if message.get("type") == "Role"]
    if len(role_messages) != 1:
        raise ValueError("Case 6A LLM node must contain exactly one Role message")
    role_messages[0]["text"] = prompt

    llm.update(
        {
            "maxRespTokens": 1200,
            "memoryEnable": True,
            "longTermMemory": False,
            "shortTermMemory": True,
            "shortTermMemoryRound": 10,
            "userPropertyEnable": False,
            "toolsEnable": False,
            "workflowEnable": False,
            "databaseEnable": False,
            "reasoningEffort": "MINIMAL",
            "showReasoning": "HIDDEN",
            "reasoningEnabled": False,
            "multiResponseTypes": ["Text"],
            "responseFormat": "Text",
            "dataEnable": True,
            "multiModalLlmInput": {"fileMode": "DISABLED", "fileSupportTypes": None},
        }
    )
    _set_temperature(llm, 0.2)

    output_path = Path(output_path)
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
    case6a_dir = generated_dir / "case6a"
    parser.add_argument("--source", type=Path, default=base_dir / "Case 6A.bot")
    parser.add_argument(
        "--prompt",
        type=Path,
        default=case6a_dir / "prompts" / "AI Model-1.md",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=generated_dir / "Case6A-Agent-K.bot",
    )
    parser.add_argument(
        "--knowledge-base-id",
        help="Bind the generated Dataset node to this GPTBots knowledge base id.",
    )
    args = parser.parse_args()
    print(
        build_agent_config(
            args.source,
            args.prompt,
            args.output,
            args.knowledge_base_id,
        )
    )


if __name__ == "__main__":
    main()
