"""
完整流程测试脚本
调用 /pdf/pages/chat，捕获所有 SSE 事件，
并详细报告 Agent A 解析、合并结果、Agent B 输入/输出。
"""

import io
import json
import re
import sys

# 强制 stdout 以 UTF-8 输出，避免 Windows GBK 编码错误
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import httpx

PDF_PATH = r"C:\Users\86137\Desktop\eBRAM3 - OCR成功\UC1_0_negotiation_intake_form (Party A).pdf"
API_URL  = "http://localhost:8000/pdf/pages/chat"

PART_A_RE = re.compile(r"===PART_A_START===(.*?)===PART_A_END===", re.DOTALL)
PART_B_RE = re.compile(r"===PART_B_START===(.*?)===PART_B_END===", re.DOTALL)

SEP = "=" * 80


def parse_agent_a(response: str) -> tuple[str, dict | None]:
    part_a = ""
    a_match = PART_A_RE.search(response)
    if a_match:
        part_a = a_match.group(1).strip()

    part_b = None
    b_match = PART_B_RE.search(response)
    if b_match:
        try:
            part_b = json.loads(b_match.group(1).strip())
        except json.JSONDecodeError as exc:
            print(f"  [警告] PART_B JSON 解析失败: {exc}")
    return part_a, part_b


def merge_part_b(part_b_list: list[dict]) -> dict:
    merged: dict = {}
    for page_data in part_b_list:
        if not isinstance(page_data, dict):
            continue
        for key, value in page_data.items():
            if key not in merged:
                merged[key] = value
            elif isinstance(value, list) and isinstance(merged[key], list):
                merged[key] = merged[key] + value
    return merged


def build_agent_b_input(summaries: list[str], merged_fields: dict) -> str:
    prefix = (
        "以下是一份法律文档的完整分析结果，包含逐页摘要和结构化提取字段，"
        "请基于这些信息进行综合分析。\n\n"
    )
    filled = [(i + 1, s) for i, s in enumerate(summaries) if s.strip()]
    summary_block = "\n\n".join(f"[Page {p}]\n{t}" for p, t in filled)
    fields_json = json.dumps(merged_fields, ensure_ascii=False, indent=2)
    return (
        f"{prefix}"
        f"## Document Summary ({len(filled)} pages)\n\n"
        f"{summary_block}\n\n"
        f"---\n\n"
        f"## Structured Data\n\n"
        f"```json\n{fields_json}\n```\n"
    )


def run():
    print(SEP)
    print("eBRAM 完整流程测试")
    print(SEP)

    with open(PDF_PATH, "rb") as f:
        pdf_bytes = f.read()

    page_results: list[dict] = []
    agent_b_result_content = ""
    sse_sequence: list[str] = []

    print("\n[1] 上传 PDF，开始 SSE 流...\n")

    with httpx.Client(timeout=600.0) as client:
        with client.stream(
            "POST",
            API_URL,
            files={"pdf_file": ("test.pdf", pdf_bytes, "application/pdf")},
        ) as resp:
            resp.raise_for_status()
            buf = ""
            for chunk in resp.iter_text():
                buf += chunk
                while "\n\n" in buf:
                    part, buf = buf.split("\n\n", 1)
                    if not part.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(part[6:])
                    except json.JSONDecodeError:
                        continue

                    ev_type = ev.get("type", "unknown")
                    sse_sequence.append(ev_type)

                    if ev_type == "stage":
                        print(f"  [SSE] stage → {ev.get('stage')} | {ev.get('message')}")
                    elif ev_type == "start":
                        print(f"  [SSE] start → 共 {ev.get('total_pages')} 页")
                    elif ev_type == "progress":
                        stage = ev.get("stage")
                        if stage == "ocr":
                            print(f"  [SSE] progress/ocr → {ev.get('extracted')}/{ev.get('total')}")
                        else:
                            print(f"  [SSE] progress/agent → 页 {ev.get('page')}/{ev.get('total')}")
                    elif ev_type == "ocr_done":
                        print(f"  [SSE] ocr_done → 成功 {ev.get('success')} 页，失败 {ev.get('failed')} 页")
                    elif ev_type == "page_result":
                        page = ev.get("page")
                        status = ev.get("status")
                        print(f"  [SSE] page_result → 第 {page} 页 [{status}]")
                        page_results.append(ev)
                    elif ev_type == "agent_b_start":
                        print(f"  [SSE] agent_b_start → {ev.get('message')}")
                    elif ev_type == "agent_b_result":
                        b_status = ev.get("status")
                        b_len = len(ev.get("content", ""))
                        print(f"  [SSE] agent_b_result → status={b_status}, 长度={b_len} 字符")
                        agent_b_result_content = ev.get("content", "")
                    elif ev_type == "complete":
                        print(f"  [SSE] complete → {ev.get('message')}")
                    elif ev_type == "error":
                        print(f"  [SSE] ERROR → {ev.get('message')}")
                    else:
                        print(f"  [SSE] {ev_type}")

    # ── Agent A 逐页解析报告 ─────────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("AGENT A — 逐页解析结果")
    print(SEP)

    summaries: list[str] = []
    part_b_list: list[dict] = []

    for r in page_results:
        page = r["page"]
        status = r["status"]
        response = r.get("agent_response", "")

        print(f"\n{'─'*60}")
        print(f"第 {page} 页  [status={status}]")
        print(f"{'─'*60}")

        if status != "success":
            print(f"  [WARN]  Agent A 调用失败：{r.get('error', '未知错误')}")
            summaries.append("")
            continue

        part_a, part_b = parse_agent_a(response)

        # 检查是否有标记
        has_part_a_marker = "===PART_A_START===" in response
        has_part_b_marker = "===PART_B_START===" in response

        if not has_part_a_marker and not has_part_b_marker:
            print("  [WARN]  该页回复中未找到 PART_A / PART_B 标记，原始回复：")
            print(f"  {response[:500]}")
            summaries.append("")
            continue

        print(f"\n  [PART A 摘要]")
        if part_a:
            print(f"  {part_a}")
        else:
            print("  （空）")

        print(f"\n  [PART B JSON]")
        if part_b is not None:
            print("  " + json.dumps(part_b, ensure_ascii=False, indent=2).replace("\n", "\n  "))
            part_b_list.append(part_b)
        else:
            print("  （解析失败或无内容）")

        summaries.append(part_a)

    # ── 合并阶段报告 ─────────────────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("合并阶段 — 摘要拼接 + 字段合并")
    print(SEP)

    print(f"\n[合并后的完整摘要（{len([s for s in summaries if s])} 页有内容）]\n")
    for i, s in enumerate(summaries, 1):
        if s:
            print(f"  [Page {i}] {s}\n")
        else:
            print(f"  [Page {i}] （空）\n")

    merged = merge_part_b(part_b_list)
    print(f"\n[合并后的完整字段表格 JSON]\n")
    print(json.dumps(merged, ensure_ascii=False, indent=2))

    agent_b_input = build_agent_b_input(summaries, merged)
    print(f"\n\n{SEP}")
    print(f"发送给 Agent B 的完整输入文本（{len(agent_b_input)} 字符）")
    print(SEP)
    print(agent_b_input)

    # ── Agent B 结果 ─────────────────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("AGENT B — 综合分析结果")
    print(SEP)
    if agent_b_result_content:
        print(agent_b_result_content)
    else:
        print("  [WARN]  未收到 Agent B 结果")

    # ── SSE 事件序列 ─────────────────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("SSE 事件序列")
    print(SEP)
    print(" → ".join(sse_sequence))

    print(f"\n\n{SEP}")
    print("测试完成")
    print(SEP)


if __name__ == "__main__":
    run()
