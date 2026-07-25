"""Run the two-stage Agent L POC through the GPTBots public API."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ALLOWED_STATUSES = {
    "FILLED",
    "NEEDS_CONFIRMATION",
    "LEAVE_BLANK",
    "KEEP_BLANK",
    "REMOVE",
}


def _http_json(
    url: str,
    api_key: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        ),
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        error = RuntimeError(f"GPTBots HTTP {exc.code}: {body[:500]}")
        error.status_code = exc.code  # type: ignore[attr-defined]
        error.delivery_unknown = exc.code >= 500  # type: ignore[attr-defined]
        raise error from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        error = RuntimeError(f"GPTBots connection failed: {exc}")
        error.status_code = None  # type: ignore[attr-defined]
        error.delivery_unknown = True  # type: ignore[attr-defined]
        raise error from exc


def _conversation_id(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for value in (
        payload.get("conversation_id"),
        payload.get("conversationId"),
        data.get("conversation_id"),
        data.get("conversationId"),
    ):
        if value:
            return str(value)
    raise RuntimeError("GPTBots did not return a conversation id")


def _collect_text(value: Any, output: list[str]) -> None:
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            text = value["text"].strip()
            if text:
                output.append(text)
        for child in value.values():
            _collect_text(child, output)
    elif isinstance(value, list):
        for child in value:
            _collect_text(child, output)


def _blocking_text(payload: dict[str, Any]) -> str:
    for container in (payload, payload.get("data")):
        if not isinstance(container, dict):
            continue
        for key in ("answer", "response", "output_text", "text", "message"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    texts: list[str] = []
    _collect_text(payload.get("output") or payload.get("data") or {}, texts)
    return "\n".join(dict.fromkeys(texts))


def _assistant_messages(payload: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for index, message in enumerate(payload.get("conversation_content", [])):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        texts: list[str] = []
        _collect_text(message.get("content", []), texts)
        if not texts:
            continue
        marker = (
            message.get("message_id")
            or message.get("messageId")
            or message.get("create_time")
            or f"assistant-{index}"
        )
        found.append((str(marker), "\n".join(dict.fromkeys(texts))))
    return found


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.S | re.I)
    if fenced:
        cleaned = fenced.group(1)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        # Keep the exact model text only in the repository's ignored output directory.
        # This makes schema regressions diagnosable without printing customer facts or
        # runtime identifiers to tracked reports and console logs.
        debug_path = (
            Path(__file__).resolve().parents[3]
            / "output"
            / "case6b-poc-invalid-agent-json.txt"
        )
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(cleaned, encoding="utf-8")
        raise
    if not isinstance(value, dict):
        raise ValueError("Agent response must be one JSON object")
    return value


class AgentLClient:
    def __init__(self, endpoint: str, api_key: str) -> None:
        self.base = f"https://api-{endpoint}.gptbots.ai"
        self.api_key = api_key

    def verify_key(self) -> None:
        _http_json(f"{self.base}/v1/api-key/verify", self.api_key)

    def create_conversation(self) -> str:
        response = _http_json(
            f"{self.base}/v1/conversation",
            self.api_key,
            method="POST",
            payload={"user_id": f"case6b-poc-{int(time.time())}"},
        )
        return _conversation_id(response)

    def messages(self, conversation_id: str) -> list[tuple[str, str]]:
        query = urllib.parse.urlencode(
            {"conversation_id": conversation_id, "page": 1, "page_size": 100}
        )
        payload = _http_json(f"{self.base}/v2/messages?{query}", self.api_key)
        return _assistant_messages(payload)

    def recover_new_message(
        self,
        conversation_id: str,
        baseline_markers: set[str],
        *,
        attempts: int = 1,
    ) -> tuple[dict[str, Any], str] | None:
        """Find a newly persisted Assistant reply without sending another user turn."""
        for attempt in range(attempts):
            recovered = self.messages(conversation_id)
            new_messages = [
                (marker, text)
                for marker, text in recovered
                if marker not in baseline_markers
            ]
            if new_messages:
                marker, text = new_messages[-1]
                return _parse_json_object(text), marker
            if attempt + 1 < attempts:
                time.sleep(2)
        return None

    def send(
        self,
        conversation_id: str,
        content: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        baseline = self.messages(conversation_id)
        baseline_markers = {marker for marker, _ in baseline}
        payload = {
            "conversation_id": conversation_id,
            "response_mode": "blocking",
            "messages": [{"role": "user", "content": content}],
            "conversation_config": {
                "long_term_memory": False,
                "short_term_memory": True,
            },
        }

        delay = 1
        for attempt in range(3):
            try:
                response = _http_json(
                    f"{self.base}/v2/conversation/message",
                    self.api_key,
                    method="POST",
                    payload=payload,
                )
            except RuntimeError as exc:
                status = getattr(exc, "status_code", None)
                recovered = self.recover_new_message(
                    conversation_id,
                    baseline_markers,
                    attempts=6 if getattr(exc, "delivery_unknown", False) else 1,
                )
                if recovered:
                    return recovered
                if status == 429 and attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                # Do not resend on timeout/5xx: it may have incremented sys_user_msg_count.
                raise

            text = _blocking_text(response)
            try:
                messages = self.messages(conversation_id)
            except RuntimeError:
                messages = baseline
            new_messages = [
                (message_marker, message_text)
                for message_marker, message_text in messages
                if message_marker not in baseline_markers
            ]
            marker = new_messages[-1][0] if new_messages else ""
            if new_messages:
                text = new_messages[-1][1]
            if not text:
                raise RuntimeError("GPTBots returned no Assistant JSON")
            return _parse_json_object(text), marker
        raise RuntimeError("GPTBots request exhausted retries")

    def trace_status(self, message_id: str) -> dict[str, Any]:
        if not message_id:
            return {"running_status": "UNKNOWN", "components": []}
        query = urllib.parse.urlencode({"msgid": message_id})
        payload = _http_json(f"{self.base}/v1/bot/logtree/query?{query}", self.api_key)
        components: list[dict[str, str]] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                name = value.get("logComponentName")
                status = value.get("runningStatus")
                if name or status:
                    components.append(
                        {"name": str(name or ""), "status": str(status or "UNKNOWN")}
                    )
                for child in value.get("children") or []:
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(payload.get("treeData") or [])
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        return {
            "running_status": str(summary.get("runningStatus") or "UNKNOWN"),
            "components": components,
        }


def _validate_field_list(payload: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    fields = payload.get("fields")
    expected_fields = expected["fields"]
    if not isinstance(fields, list):
        return ["template parser did not return fields[]"]
    actual_ids = [field.get("field_id") for field in fields if isinstance(field, dict)]
    expected_ids = [field["field_id"] for field in expected_fields]
    if actual_ids != expected_ids:
        failures.append("template parser field ids/order differ from the 37-field manifest")
    required_keys = {
        "field_id",
        "semantic_key",
        "label",
        "field_kind",
        "group_key",
        "group_index",
        "required",
        "resolution_policy",
    }
    for field in fields:
        if not isinstance(field, dict) or not required_keys.issubset(field):
            failures.append("template parser returned an incomplete field object")
            break
        if "value" in field:
            failures.append("template parser improperly filled a value")
            break
    return failures


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower().replace("×", "x"))


def _validate_fill(payload: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    fields = payload.get("fields")
    expected_fields = expected["fields"]
    if not isinstance(fields, list):
        return ["field filler did not return fields[]"]
    actual_ids = [field.get("field_id") for field in fields if isinstance(field, dict)]
    expected_ids = [field["field_id"] for field in expected_fields]
    if actual_ids != expected_ids:
        failures.append("field filler field ids/order differ from the 37-field manifest")
        return failures

    expected_by_id = {field["field_id"]: field for field in expected_fields}
    for field in fields:
        expected_field = expected_by_id[field["field_id"]]
        status = field.get("status")
        if status not in ALLOWED_STATUSES:
            failures.append(f"{field['field_id']}: invalid status {status!r}")
            continue
        if status != expected_field["status"]:
            failures.append(
                f"{field['field_id']}: expected {expected_field['status']}, got {status}"
            )
        expected_action = expected_field.get("service_action")
        if expected_action and field.get("service_action") != expected_action:
            failures.append(
                f"{field['field_id']}: expected service_action "
                f"{expected_action}, got {field.get('service_action')}"
            )
        if status == "FILLED":
            if _normalize(field.get("value")) != _normalize(expected_field["value"]):
                failures.append(f"{field['field_id']}: filled value differs from fixture")
            if not field.get("evidence"):
                failures.append(f"{field['field_id']}: filled value has no evidence")
        elif field.get("value") not in ("", None):
            failures.append(f"{field['field_id']}: non-filled status must have empty value")
    return failures


def _placeholder_input(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "template_filename": manifest["template_filename"],
        "template_language": manifest["template_language"],
        "template_placeholders": [
            {
                "field_id": field["field_id"],
                "locator": field["locator"],
                "context": field["context"],
                "group_index_hint": field["group_index"],
            }
            for field in manifest["fields"]
        ],
    }


def _write_evaluation(
    *,
    args: argparse.Namespace,
    field_list: dict[str, Any],
    fill_result: dict[str, Any],
    first_trace: dict[str, Any],
    second_trace: dict[str, Any],
    failures: list[str],
) -> None:
    raw_report = {
        "generated_at_epoch": int(time.time()),
        "endpoint": args.endpoint,
        "field_list": field_list,
        "fill_result": fill_result,
        "traces": {"template_parse": first_trace, "field_fill": second_trace},
        "failures": failures,
        "passed": not failures,
    }
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_text(
        json.dumps(raw_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    status = "Passed" if not failures else "Failed"
    failure_lines = "\n".join(f"- {failure}" for failure in failures) or "- None"
    args.summary_output.write_text(
        "\n".join(
            [
                "# Agent L POC Evaluation",
                "",
                f"Status: {status}",
                "",
                f"- Evaluation date: {time.strftime('%Y-%m-%d')}",
                "- Conversation model: one private conversation, two blocking messages",
                f"- Template fields returned: {len(field_list.get('fields', []))}",
                f"- Filled field results returned: {len(fill_result.get('fields', []))}",
                f"- Template parse trace: {first_trace['running_status']}",
                f"- Field fill trace: {second_trace['running_status']}",
                "",
                "## Failures",
                "",
                failure_lines,
                "",
                "Full model responses and runtime identifiers are stored under ignored `output/`.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Agent L POC: {status}; sanitized summary: {args.summary_output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    case6b_dir = Path(__file__).resolve().parent
    repo_root = Path(__file__).resolve().parents[3]
    fixtures = case6b_dir / "fixtures"
    sample_dir = repo_root / "input_example" / "Usecase 6B" / "Input documents"
    parser.add_argument("--endpoint", choices=["sg", "jp", "th"], default="sg")
    parser.add_argument(
        "--template",
        type=Path,
        default=sample_dir / "UC6B_0_Format - Service Agreement.docx",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        default=repo_root / "output" / "case6b-poc-latest.json",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=case6b_dir / "evaluation-summary.md",
    )
    args = parser.parse_args()

    load_dotenv(repo_root / ".env", override=False)
    api_key = os.environ.get("AGENT_L_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Set AGENT_L_API_KEY in the process environment or ignored local .env")

    manifest = json.loads(
        (fixtures / "template_field_manifest.json").read_text(encoding="utf-8")
    )
    summary = json.loads((fixtures / "full_summary.json").read_text(encoding="utf-8"))
    expected = json.loads((fixtures / "expected_fill.json").read_text(encoding="utf-8"))
    template_b64 = base64.b64encode(args.template.read_bytes()).decode("ascii")

    client = AgentLClient(args.endpoint, api_key)
    client.verify_key()
    conversation_id = client.create_conversation()

    first_context = "\n".join(
        [
            "[CASE6B_PHASE:TEMPLATE_PARSE]",
            "",
            "# Case 6B template context",
            "",
            "## Document identity",
            "",
            "- logical_filename: `case6b_template_context.md`",
            "- document_role: `template_context`",
            "- attachment_position: `2`",
            "",
            "## Detected fields",
            "",
            "```json",
            json.dumps(_placeholder_input(manifest), ensure_ascii=False, indent=2),
            "```",
        ]
    ).encode("utf-8")
    field_list, first_message_id = client.send(
        conversation_id,
        [
            {
                "type": "text",
                "text": (
                    "[CASE6B_PHASE:TEMPLATE_PARSE]\n"
                    f"The first document attachment (DOCX; logical name "
                    f"`{args.template.name}`) is the original agreement template. "
                    "The second document attachment (Markdown; document role "
                    "`template_context`) contains the detected placeholders, stable "
                    "field IDs, locators, and required output schema. GPTBots may show "
                    "generated attachment names; identify files by order, format, phase "
                    "marker and the internal Document identity header. "
                    "output schema. Read both attachments together without adding, "
                    "removing, reordering, or filling fields. Return exactly one JSON "
                    "Object and no commentary or Markdown fences."
                ),
            },
            {
                "type": "document",
                "document": [
                    {
                        "base64_content": template_b64,
                        "format": "docx",
                        "name": args.template.name,
                    },
                    {
                        "base64_content": base64.b64encode(first_context).decode(
                            "ascii"
                        ),
                        "format": "md",
                        "name": "case6b_template_context.md",
                    }
                ],
            },
        ],
    )
    failures = _validate_field_list(field_list, manifest)
    first_trace = client.trace_status(first_message_id)
    if failures:
        _write_evaluation(
            args=args,
            field_list=field_list,
            fill_result={},
            first_trace=first_trace,
            second_trace={"running_status": "NOT_RUN", "components": []},
            failures=failures,
        )
        raise SystemExit(
            "Agent L template parsing failed; field filling was not sent to preserve phase order"
        )

    second_context = "\n".join(
        [
            "[CASE6B_PHASE:FIELD_FILL]",
            "",
            "# Case 6B field-fill context",
            "",
            "## Document identity",
            "",
            "- logical_filename: `case6b_field_fill_context.md`",
            "- document_role: `field_fill_context`",
            "- attachment_position: `1`",
            "",
            "## Field list",
            "",
            "```json",
            json.dumps(field_list, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Evidence summary",
            "",
            "```json",
            json.dumps(summary, ensure_ascii=False, indent=2),
            "```",
        ]
    ).encode("utf-8")
    fill_result, second_message_id = client.send(
        conversation_id,
        [
            {
                "type": "text",
                "text": (
                    "[CASE6B_PHASE:FIELD_FILL]\n"
                    "The only document attachment (Markdown; document role "
                    "`field_fill_context`) contains the detected field list and evidence "
                    "summary from all source files. GPTBots may show a generated filename; "
                    "identify it by the phase marker and internal Document identity header. "
                    "Treat it as "
                    "the sole source of field IDs and facts, fill every listed field "
                    "once, and preserve evidence references. Return exactly one JSON "
                    "Object and no commentary or Markdown fences."
                ),
            },
            {
                "type": "document",
                "document": [
                    {
                        "base64_content": base64.b64encode(second_context).decode(
                            "ascii"
                        ),
                        "format": "md",
                        "name": "case6b_field_fill_context.md",
                    }
                ],
            }
        ],
    )
    failures.extend(_validate_fill(fill_result, expected))
    second_trace = client.trace_status(second_message_id)

    for label, trace in (("template_parse", first_trace), ("field_fill", second_trace)):
        if trace["running_status"] not in {"SUCCESS", "UNKNOWN"}:
            failures.append(f"{label}: LogTree status {trace['running_status']}")

    _write_evaluation(
        args=args,
        field_list=field_list,
        fill_result=fill_result,
        first_trace=first_trace,
        second_trace=second_trace,
        failures=failures,
    )
    if failures:
        raise SystemExit("Agent L POC failed; see the sanitized evaluation summary")


if __name__ == "__main__":
    main()
