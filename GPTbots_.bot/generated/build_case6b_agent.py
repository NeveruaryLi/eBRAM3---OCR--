"""Regenerate Agent L from the exported Case 6B FlowAgent baseline."""

from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path


EXPECTED_FLOW = {
    1: ("Input", "User Input"),
    2: ("Output", "Output"),
    3: ("Regular", "Regular-1"),
    4: ("LLM", "AI Model-1"),
    6: ("LLM", "AI Model-2"),
}


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


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
        raise ValueError("Case 6B source config must be a FlowAgent")
    components = config.get("flowRule", {}).get("components", [])
    by_id = {component.get("id"): component for component in components}
    if set(by_id) != set(EXPECTED_FLOW):
        raise ValueError("Case 6B source must contain exactly the five expected nodes")
    for component_id, expected in EXPECTED_FLOW.items():
        actual = (by_id[component_id].get("type"), by_id[component_id].get("name"))
        if actual != expected:
            raise ValueError(f"Unexpected component {component_id}: {actual!r}")
    return by_id


def _load_prompts(prompt_dir: Path) -> dict[str, str]:
    prompts = {}
    for name in ("AI Model-1", "AI Model-2"):
        path = Path(prompt_dir) / f"{name}.md"
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError(f"Agent L prompt must not be empty: {path}")
        prompts[name] = value
    return prompts


def _set_role_prompt(component: dict, prompt: str) -> None:
    role_messages = [
        message for message in component.get("messages", []) if message.get("type") == "Role"
    ]
    if len(role_messages) != 1:
        raise ValueError(f"{component.get('name')} must contain exactly one Role message")
    role_messages[0]["text"] = prompt


def _configure_llm(component: dict, prompt: str, *, accepts_document: bool) -> None:
    _set_role_prompt(component, prompt)
    component.update(
        {
            "maxRespTokens": 12000,
            "memoryEnable": True,
            "longTermMemory": False,
            "shortTermMemory": True,
            "shortTermMemoryRound": 2,
            "userPropertyEnable": False,
            "userPropertyIds": [],
            "toolsEnable": False,
            "workflowEnable": False,
            "databaseEnable": False,
            "associatedWorkflows": [],
            "databaseTableIds": [],
            "keyEventConfig": None,
            "reasoningEffort": "LOW",
            "showReasoning": "HIDDEN",
            "reasoningEnabled": True,
            "multiResponseTypes": ["Text"],
            "responseFormat": "JsonObject",
            "jsonSchema": None,
            "exceptionSwitch": False,
            "multiModalLlmInput": (
                {"fileMode": "SYSTEM", "fileSupportTypes": ["Document"]}
                if accepts_document
                else {"fileMode": "DISABLED", "fileSupportTypes": None}
            ),
        }
    )
    _set_temperature(component, 0.1)


def build_agent_config(source_path: Path, prompt_dir: Path, output_path: Path) -> Path:
    """Clone the exported FlowAgent and apply the approved Agent L settings."""
    prompts = _load_prompts(Path(prompt_dir))
    config = copy.deepcopy(_read_json(Path(source_path)))
    components = _assert_baseline(config)

    config.update(
        {
            "exportTime": int(time.time() * 1000),
            "name": "Agent L - Service Agreement Drafting",
            "introduction": (
                "Internal two-stage FlowAgent for parsing DOCX template fields and "
                "filling them from a curated evidence summary."
            ),
            "maxRespTokens": 12000,
            "reasoningEffort": "LOW",
            "showReasoning": "HIDDEN",
            "reasoningEnabled": True,
            "memoryEnable": True,
            "longTermMemory": False,
            "shortTermMemory": True,
            "shortTermMemoryRound": 2,
            "userPropertyEnable": False,
            "toolsEnable": False,
            "showTools": False,
            "workflowEnable": False,
            "showWorkflow": False,
            "associatedWorkflows": [],
            "databaseEnable": False,
            "multiResponseTypes": ["Text"],
            "responseFormat": "JsonObject",
            "jsonSchema": None,
            "nextQuestion": False,
            "firstMessage": "",
        }
    )
    _set_temperature(config, 0.1)

    multimodal_input = config.setdefault("multiModal", {}).setdefault("multiModalInput", {})
    multimodal_input.update(
        {
            "fileLimit": 1,
            "fileMode": "SYSTEM",
            "fileSupportTypes": ["Document"],
            "fileSwitch": True,
            "textSwitch": True,
        }
    )
    config.setdefault("multiModal", {}).setdefault("multiModalOutput", {})["textSwitch"] = True

    for component in components.values():
        if "longTermMemory" in component:
            component["longTermMemory"] = False
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

    regular = components[3]
    groups = regular.get("regularGroups") or []
    if len(groups) != 1 or len(groups[0].get("items") or []) != 1:
        raise ValueError("Case 6B Regular node must contain exactly one rule")
    rule = groups[0]["items"][0]
    if rule.get("propertyKey") != "sys_user_msg_count":
        raise ValueError("Case 6B Regular node must route on sys_user_msg_count")
    # Live LogTree verification shows that the Regular node sees the number of
    # user turns completed *before* the current message: first turn = 0,
    # second turn = 1. The original exported `lt 1` rule therefore routes only
    # TEMPLATE_PARSE to Model-1 and every later turn to Model-2.
    rule.update({"category": "GlobalVariable", "type": "number", "op": "lt", "value": "1"})

    _configure_llm(
        components[4],
        prompts["AI Model-1"],
        accepts_document=True,
    )
    _configure_llm(
        components[6],
        prompts["AI Model-2"],
        accepts_document=False,
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    generated_dir = Path(__file__).resolve().parent
    base_dir = generated_dir.parent
    case6b_dir = generated_dir / "case6b"
    parser.add_argument("--source", type=Path, default=base_dir / "EBRAM-Case6B.bot")
    parser.add_argument("--prompts", type=Path, default=case6b_dir / "prompts")
    parser.add_argument("--output", type=Path, default=generated_dir / "Case6B-Agent-L.bot")
    args = parser.parse_args()
    print(build_agent_config(args.source, args.prompts, args.output))


if __name__ == "__main__":
    main()
