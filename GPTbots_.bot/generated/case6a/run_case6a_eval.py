"""Run the Case 6A Agent K acceptance set through the GPTBots public API."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


SIMPLIFIED_ONLY_MARKERS = set("这为发后里开关从个资应与门络证费务")


def _request(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GPTBots HTTP {exc.code}: {body[:500]}") from exc


def _get(url: str, api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GPTBots HTTP {exc.code}: {body[:500]}") from exc


def _conversation_id(payload: dict[str, Any]) -> str:
    candidates = [
        payload.get("conversation_id"),
        payload.get("conversationId"),
        (payload.get("data") or {}).get("conversation_id") if isinstance(payload.get("data"), dict) else None,
        (payload.get("data") or {}).get("conversationId") if isinstance(payload.get("data"), dict) else None,
    ]
    value = next((item for item in candidates if item), None)
    if not value:
        raise RuntimeError(f"Conversation id missing from response: {payload}")
    return str(value)


def _assistant_text(payload: dict[str, Any]) -> str:
    direct_keys = ("answer", "response", "output_text", "text", "message")
    for key in direct_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    data = payload.get("data")
    if isinstance(data, dict):
        for key in direct_keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    texts: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "text" and isinstance(value.get("text"), str):
                texts.append(value["text"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload.get("output") or data or payload)
    unique = list(dict.fromkeys(text.strip() for text in texts if text.strip()))
    return "\n".join(unique)


def _assistant_from_messages(payload: dict[str, Any]) -> str:
    for message in payload.get("conversation_content", []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        texts: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                if value.get("type") == "text" and isinstance(value.get("text"), str):
                    texts.append(value["text"].strip())
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(message.get("content", []))
        if texts:
            return "\n".join(dict.fromkeys(text for text in texts if text))
    return ""


class AgentClient:
    def __init__(self, endpoint: str, api_key: str) -> None:
        self.base = f"https://api-{endpoint}.gptbots.ai"
        self.api_key = api_key

    def create_conversation(self, user_id: str) -> str:
        payload = _request(
            f"{self.base}/v1/conversation",
            self.api_key,
            {"user_id": user_id},
        )
        return _conversation_id(payload)

    def send(self, conversation_id: str, text: str) -> tuple[str, dict[str, Any]]:
        payload = _request(
            f"{self.base}/v2/conversation/message",
            self.api_key,
            {
                "conversation_id": conversation_id,
                "response_mode": "blocking",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": text}],
                    }
                ],
                "conversation_config": {
                    "long_term_memory": False,
                    "short_term_memory": True,
                },
            },
        )
        answer = _assistant_text(payload)
        if not answer:
            query = urllib.parse.urlencode(
                {"conversation_id": conversation_id, "page": 1, "page_size": 100}
            )
            detail = _get(f"{self.base}/v2/messages?{query}", self.api_key)
            answer = _assistant_from_messages(detail)
        return answer, payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    base_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--questions",
        type=Path,
        default=base_dir / "tests" / "acceptance_questions.json",
    )
    parser.add_argument("--endpoint", choices=["sg", "jp", "th"], default="sg")
    parser.add_argument(
        "--output",
        type=Path,
        default=base_dir / "evaluation" / "latest.json",
    )
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
    api_key = (
        os.environ.get("AGENT_K_API_KEY", "").strip()
        or os.environ.get("GPTBOTS_API_KEY", "").strip()
    )
    if not api_key:
        raise SystemExit("Set AGENT_K_API_KEY in the current process environment")

    suite = json.loads(args.questions.read_text(encoding="utf-8"))
    client = AgentClient(args.endpoint, api_key)
    stamp = int(time.time())
    results: list[dict[str, Any]] = []
    failures: list[str] = []

    for case in suite["questions"]:
        conversation_id = client.create_conversation(f"case6a-eval-{stamp}-{case['id'].lower()}")
        answer, raw = client.send(conversation_id, case["text"])
        checks = {
            "non_empty": bool(answer.strip()),
            "no_internal_agent_code": "agent k" not in answer.lower(),
        }
        if case["kind"] == "grounded":
            checks["official_source"] = "ebram.org" in answer.lower()
        elif case["kind"] == "off_topic":
            checks["scope_response"] = "ebram" in answer.lower()
            checks["no_contact_fallback"] = "contact_us" not in answer.lower()
            checks["no_stiff_self_introduction"] = not answer.lower().lstrip().startswith("i am")
        elif case["kind"] == "knowledge_gap":
            checks["contact_fallback"] = "contact_us" in answer.lower()
        elif case["kind"] == "traditional_chinese":
            checks["official_source"] = "ebram.org" in answer.lower()
            checks["traditional_chinese"] = not any(char in SIMPLIFIED_ONLY_MARKERS for char in answer)
        elif case["kind"] == "identity":
            lower_answer = answer.lower()
            checks["scope_response"] = "ebram" in lower_answer
            checks["legal_advice_boundary"] = (
                "legal advice" in lower_answer
                and any(marker in lower_answer for marker in ("cannot", "can't", "do not", "not able"))
            )
        passed = all(checks.values())
        if not passed:
            failures.append(case["id"])
        results.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "question": case["text"],
                "conversation_id": conversation_id,
                "answer": answer,
                "checks": checks,
                "passed": passed,
                "response_keys": sorted(raw.keys()),
            }
        )
        print(f"{case['id']}: {answer[:160].replace(chr(10), ' ')}")

    conversation_id = client.create_conversation(f"case6a-eval-{stamp}-multiturn")
    first_answer, _ = client.send(conversation_id, suite["multi_turn"]["first"])
    follow_up_answer, _ = client.send(conversation_id, suite["multi_turn"]["follow_up"])
    new_conversation_id = client.create_conversation(f"case6a-eval-{stamp}-isolation")
    isolation_answer, _ = client.send(new_conversation_id, suite["multi_turn"]["follow_up"])
    multi_turn_checks = {
        "first_non_empty": bool(first_answer.strip()),
        "follow_up_non_empty": bool(follow_up_answer.strip()),
        "follow_up_official_source": "ebram.org" in follow_up_answer.lower(),
        "new_conversation_is_distinct": new_conversation_id != conversation_id,
        "new_conversation_answer_non_empty": bool(isolation_answer.strip()),
        "no_internal_agent_code": "agent k" not in (
            first_answer + follow_up_answer + isolation_answer
        ).lower(),
    }
    if not all(multi_turn_checks.values()):
        failures.append("MULTI_TURN")

    report = {
        "generated_at_epoch": stamp,
        "endpoint": args.endpoint,
        "results": results,
        "multi_turn": {
            "conversation_id": conversation_id,
            "first_question": suite["multi_turn"]["first"],
            "first_answer": first_answer,
            "follow_up_question": suite["multi_turn"]["follow_up"],
            "follow_up_answer": follow_up_answer,
            "checks": multi_turn_checks,
        },
        "conversation_isolation": {
            "conversation_id": new_conversation_id,
            "question": suite["multi_turn"]["follow_up"],
            "answer": isolation_answer,
        },
        "passed": not failures,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.output}")
    if failures:
        raise SystemExit(f"Agent K acceptance failed: {', '.join(failures)}")


if __name__ == "__main__":
    main()
