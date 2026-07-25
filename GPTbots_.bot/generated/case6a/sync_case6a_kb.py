"""Create, populate, test and bind the Case 6A GPTBots knowledge base.

Only GPTBots public API endpoints are used. The Agent key is read from the
AGENT_K_API_KEY process environment variable and is never serialized.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv


KB_NAME = "eBRAM Website Support KB (Case 6A Test)"
KB_DESCRIPTION = (
    "Curated official eBRAM website and official attachment content for the "
    "Case 6A Agent K test-mode customer-support assistant."
)
UPLOAD_BATCH_SIZE = 10  # The live API rejects 20 even though the reference says up to 20.
EXPECTED_SOURCES = {
    "Q01": ("about-profile", "about-services", "platform-terms-security", "online-arbitration", "online-mediation", "apec-odr"),
    "Q02": ("online-arbitration", "online-mediation", "odr-services-platforms-guide"),
    "Q03": ("platform-terms-security", "odr-services-platforms-guide"),
    "Q04": ("mediator-training", "panel-application", "codes-and-guidelines"),
    "Q05": ("support-and-contact", "contact_us"),
    "Q06": ("arbitration-rules", "mediation-rules", "apec-rules", "rules-procedures-guide"),
    "Q07": ("deal-making", "model-clauses"),
    "Q08": ("machine-document-translation", "real-time-captions"),
    "Q09": ("apec-odr", "apec-rules", "online-arbitration", "online-mediation"),
}


class GPTBotsError(RuntimeError):
    pass


class Client:
    def __init__(self, endpoint: str, api_key: str, timeout: int = 180) -> None:
        self.base = f"https://api-{endpoint}.gptbots.ai"
        self.api_key = api_key
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        query: list[tuple[str, str]] | dict[str, Any] | None = None,
    ) -> Any:
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GPTBotsError(f"{method} {path} returned HTTP {exc.code}: {body[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise GPTBotsError(f"{method} {path} failed: {exc}") from exc


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def first_value(payload: Any, *keys: str) -> str | None:
    for item in walk_dicts(payload):
        for key in keys:
            value = item.get(key)
            if isinstance(value, (str, int)) and str(value):
                return str(value)
    return None


def list_knowledge_bases(client: Client) -> list[dict[str, Any]]:
    payload = client.request("GET", "/v1/bot/knowledge/base/page")
    if isinstance(payload, dict) and isinstance(payload.get("knowledge_base"), list):
        return [item for item in payload["knowledge_base"] if isinstance(item, dict)]
    return [item for item in walk_dicts(payload) if item.get("knowledge_base_id")]


def list_documents(client: Client, knowledge_base_id: str) -> list[dict[str, Any]]:
    page = 1
    documents: dict[str, dict[str, Any]] = {}
    while True:
        payload = client.request(
            "GET",
            "/v1/bot/doc/query/page",
            query={"knowledge_base_id": knowledge_base_id, "page": page, "page_size": 100},
        )
        if isinstance(payload, dict) and isinstance(payload.get("list"), list):
            found = [item for item in payload["list"] if isinstance(item, dict)]
        else:
            found = [item for item in walk_dicts(payload) if item.get("doc_id") or item.get("data_id")]
        for item in found:
            doc_id = str(item.get("doc_id") or item.get("data_id") or item.get("id"))
            documents[doc_id] = item
        if len(found) < 100:
            break
        page += 1
    return list(documents.values())


def _kb_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or item.get("knowledge_base_name") or item.get("group_name") or "")


def _kb_id(item: dict[str, Any]) -> str:
    return str(item.get("knowledge_base_id") or item.get("group_id") or item.get("id") or "")


def ensure_knowledge_base(
    client: Client,
    local_hashes: dict[str, str],
    prior_state: dict[str, Any] | None,
) -> tuple[str, str, list[dict[str, Any]]]:
    local_names = set(local_hashes)
    knowledge_bases = list_knowledge_bases(client)
    matching = [item for item in knowledge_bases if _kb_name(item) == KB_NAME]
    for item in matching:
        knowledge_base_id = _kb_id(item)
        cloud_docs = list_documents(client, knowledge_base_id)
        cloud_names = {
            str(doc.get("doc_name") or doc.get("file_name") or doc.get("name") or "")
            for doc in cloud_docs
        }
        state_matches = bool(
            prior_state
            and prior_state.get("knowledge_base_id") == knowledge_base_id
            and {
                item.get("filename"): item.get("content_sha256")
                for item in prior_state.get("documents", [])
            }
            == local_hashes
        )
        if not cloud_names or (cloud_names == local_names and state_matches):
            return knowledge_base_id, KB_NAME, cloud_docs

    occupied = {_kb_name(item) for item in knowledge_bases}
    name = KB_NAME
    revision = 2
    while name in occupied:
        name = f"{KB_NAME} r{revision}"
        revision += 1
    payload = client.request(
        "POST",
        "/v1/bot/knowledge/base/create",
        payload={
            "name": name,
            "desc": KB_DESCRIPTION,
            "graph_enable": False,
            "access_control_enabled": False,
        },
    )
    knowledge_base_id = first_value(payload, "knowledge_base_id", "group_id")
    if not knowledge_base_id:
        raise GPTBotsError(f"Knowledge-base id missing from create response: {payload}")
    return knowledge_base_id, name, []


def read_manifest(kb_dir: Path) -> list[dict[str, str]]:
    with (kb_dir / "source_manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or any(row.get("status") != "ready" for row in rows):
        raise SystemExit("Case_6A_KB/source_manifest.csv is missing or contains failed sources")
    missing = [row["filename"] for row in rows if not (kb_dir / "docs" / row["filename"]).is_file()]
    if missing:
        raise SystemExit(f"Manifest documents are missing: {', '.join(missing)}")
    return rows


def upload_documents(
    client: Client,
    knowledge_base_id: str,
    kb_dir: Path,
    rows: list[dict[str, str]],
    existing: list[dict[str, Any]],
) -> list[dict[str, str]]:
    existing_by_name = {
        str(item.get("doc_name") or item.get("file_name") or item.get("name") or ""): item
        for item in existing
    }
    records: list[dict[str, str]] = []
    pending = [row for row in rows if row["filename"] not in existing_by_name]
    for row in rows:
        item = existing_by_name.get(row["filename"])
        if item:
            records.append(
                {
                    "filename": row["filename"],
                    "doc_id": str(item.get("doc_id") or item.get("data_id") or item.get("id")),
                    "content_sha256": row["content_sha256"],
                    "status": "existing",
                }
            )

    for offset in range(0, len(pending), UPLOAD_BATCH_SIZE):
        batch = pending[offset : offset + UPLOAD_BATCH_SIZE]
        files = []
        for row in batch:
            path = kb_dir / "docs" / row["filename"]
            files.append(
                {
                    "file_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                    "source_url": row["resolved_url"],
                    "file_name": row["filename"],
                }
            )
        payload = client.request(
            "POST",
            "/v1/bot/doc/text/add",
            payload={"knowledge_base_id": knowledge_base_id, "chunk_token": 700, "files": files},
        )
        failed = set(payload.get("failed") or []) if isinstance(payload, dict) else set()
        returned = {
            str(item.get("doc_name")): str(item.get("doc_id"))
            for item in walk_dicts(payload)
            if item.get("doc_name") and item.get("doc_id")
        }
        for row in batch:
            name = row["filename"]
            doc_id = returned.get(name)
            if name in failed or not doc_id:
                raise GPTBotsError(f"Document upload did not return a doc_id for {name}: {payload}")
            records.append(
                {
                    "filename": name,
                    "doc_id": doc_id,
                    "content_sha256": row["content_sha256"],
                    "status": "uploaded",
                }
            )
    return sorted(records, key=lambda item: item["filename"])


def wait_for_documents(client: Client, records: list[dict[str, str]], timeout: int) -> None:
    doc_ids = [record["doc_id"] for record in records]
    deadline = time.monotonic() + timeout
    retried = False
    while True:
        statuses: dict[str, str] = {}
        for offset in range(0, len(doc_ids), 50):
            query = [("data_ids", doc_id) for doc_id in doc_ids[offset : offset + 50]]
            payload = client.request("GET", "/v1/bot/data/detail/list", query=query)
            for item in walk_dicts(payload):
                if item.get("data_id") and item.get("data_status"):
                    statuses[str(item["data_id"])] = str(item["data_status"]).upper()
        failed = [doc_id for doc_id, status in statuses.items() if status in {"FAILED", "FAILURE"}]
        if failed and not retried:
            client.request("POST", "/v1/bot/data/retry/batch", payload={})
            retried = True
            time.sleep(5)
            continue
        if failed:
            raise GPTBotsError(f"Knowledge documents failed after retry: {failed}")
        if statuses and all(status == "AVAILABLE" for status in statuses.values()) and len(statuses) == len(doc_ids):
            for record in records:
                record["status"] = "AVAILABLE"
            return
        if time.monotonic() >= deadline:
            raise GPTBotsError(f"Timed out waiting for knowledge documents: {statuses}")
        print(f"Waiting for vectorization: {sum(v == 'AVAILABLE' for v in statuses.values())}/{len(doc_ids)}")
        time.sleep(10)


def retrieval_tests(client: Client, knowledge_base_id: str, questions_path: Path) -> dict[str, Any]:
    suite = json.loads(questions_path.read_text(encoding="utf-8"))
    results = []
    failures = []
    for case in suite["questions"]:
        if not (case["id"].startswith("Q") and case["id"][1:].isdigit()):
            continue
        payload = client.request(
            "POST",
            "/v1/vector/match",
            payload={
                "embedding_rate": 0.9,
                "prompt": case["text"],
                "group_ids": [knowledge_base_id],
                "top_k": 5,
                "rerank_version": "BGE-Rerank",
                "doc_correlation": 0.76,
            },
        )
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        expected = EXPECTED_SOURCES.get(case["id"], ())
        matched = [source for source in expected if source.lower() in serialized]
        hit_dicts = [item for item in walk_dicts(payload) if any(key in item for key in ("content", "text", "doc_name", "source_url"))]
        passed = not hit_dicts if case["id"] == "Q10" else bool(matched)
        if not passed:
            failures.append(case["id"])
        results.append(
            {
                "id": case["id"],
                "question": case["text"],
                "passed": passed,
                "expected_source_tokens": list(expected),
                "matched_source_tokens": matched,
                "result_count": len(hit_dicts),
                "response": payload,
            }
        )
        print(f"Retrieval {case['id']}: {'PASS' if passed else 'FAIL'}")
    return {"passed": not failures, "failures": failures, "results": results}


def write_state(
    path: Path,
    *,
    knowledge_base_id: str,
    knowledge_base_name: str,
    records: list[dict[str, str]],
    retrieval: dict[str, Any] | None = None,
) -> None:
    path.write_text(
        json.dumps(
            {
                "knowledge_base_id": knowledge_base_id,
                "knowledge_base_name": knowledge_base_name,
                "documents": records,
                "retrieval": retrieval,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def read_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def bind_and_publish(project_root: Path, knowledge_base_id: str, endpoint: str, api_key: str) -> None:
    generated = project_root / "GPTbots_.bot" / "generated"
    bot_path = generated / "Case6A-Agent-K.bot"
    subprocess.run(
        [
            sys.executable,
            str(generated / "build_case6a_agent.py"),
            "--knowledge-base-id",
            knowledge_base_id,
        ],
        cwd=project_root,
        check=True,
    )
    skill_root = Path.home() / ".agents" / "skills" / "gptbots-agent-skill"
    validator = skill_root / "scripts" / "validate_gptbots_config.py"
    subprocess.run([sys.executable, str(validator), str(bot_path)], cwd=project_root, check=True)
    publish_env = os.environ.copy()
    publish_env["GPTBOTS_API_KEY"] = api_key
    subprocess.run(
        [
            sys.executable,
            str(skill_root / "scripts" / "publish_gptbots.py"),
            str(bot_path),
            "--endpoint",
            endpoint,
            "--release",
            "--version-desc",
            "Case 6A Agent K with curated eBRAM website knowledge base",
        ],
        cwd=project_root,
        env=publish_env,
        check=True,
    )


def run_agent_acceptance(project_root: Path, endpoint: str, api_key: str) -> None:
    eval_script = project_root / "GPTbots_.bot" / "generated" / "case6a" / "run_case6a_eval.py"
    eval_env = os.environ.copy()
    eval_env["AGENT_K_API_KEY"] = api_key
    subprocess.run(
        [sys.executable, str(eval_script), "--endpoint", endpoint],
        cwd=project_root,
        env=eval_env,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    project_root = Path(__file__).resolve().parents[3]
    parser.add_argument("--endpoint", choices=["sg", "jp", "th"], default="sg")
    parser.add_argument("--kb-dir", type=Path, default=project_root / "Case_6A_KB")
    parser.add_argument("--questions", type=Path, default=Path(__file__).parent / "tests" / "acceptance_questions.json")
    parser.add_argument("--vector-timeout", type=int, default=1200)
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()

    load_dotenv(project_root / ".env", override=False)
    api_key = os.environ.get("AGENT_K_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Set AGENT_K_API_KEY in the current process environment")
    client = Client(args.endpoint, api_key)
    verified = client.request("GET", "/v1/api-key/verify")
    print(f"Verified Agent key ({first_value(verified, 'type', 'entity_type') or 'agent'})")

    rows = read_manifest(args.kb_dir)
    local_hashes = {row["filename"]: row["content_sha256"] for row in rows}
    local_names = set(local_hashes)
    state_path = args.kb_dir / "upload-state.json"
    knowledge_base_id, knowledge_base_name, existing = ensure_knowledge_base(
        client,
        local_hashes,
        read_state(state_path),
    )
    print(f"Knowledge base: {knowledge_base_name} ({knowledge_base_id})")
    records = upload_documents(client, knowledge_base_id, args.kb_dir, rows, existing)
    write_state(
        state_path,
        knowledge_base_id=knowledge_base_id,
        knowledge_base_name=knowledge_base_name,
        records=records,
    )
    wait_for_documents(client, records, args.vector_timeout)
    cloud_names = {
        str(item.get("doc_name") or item.get("file_name") or item.get("name") or "")
        for item in list_documents(client, knowledge_base_id)
    }
    if cloud_names != local_names:
        raise GPTBotsError(f"Cloud/local document mismatch: cloud={len(cloud_names)} local={len(local_names)}")

    retrieval = retrieval_tests(client, knowledge_base_id, args.questions)
    write_state(
        state_path,
        knowledge_base_id=knowledge_base_id,
        knowledge_base_name=knowledge_base_name,
        records=records,
        retrieval=retrieval,
    )
    if not retrieval["passed"]:
        raise GPTBotsError(f"Retrieval acceptance failed: {retrieval['failures']}")
    if not args.no_publish:
        bind_and_publish(project_root, knowledge_base_id, args.endpoint, api_key)
        run_agent_acceptance(project_root, args.endpoint, api_key)
    print("Case 6A knowledge sync completed")


if __name__ == "__main__":
    main()
