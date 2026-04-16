"""
多文档流程测试脚本
测试两份 PDF 的完整多文档流程：
  1. PDF1 → /pdf/pages/chat (session_id=X) → Agent A → doc_complete
  2. PDF2 → /pdf/pages/chat (session_id=X) → Agent A → doc_complete
  3. /pdf/session/analyze (session_id=X) → Agent B 多轮 → analysis_result

打印：
  - 每份文档 Agent A 逐页摘要（PART_A）和字段（PART_B）
  - 发给 Agent B 的两条材料消息（重建）
  - Agent B 的最终回复
"""

import io
import json
import re
import sys
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import httpx

PDF1 = r"C:\Users\86137\Desktop\eBRAM3 - OCR成功\UC1_0_negotiation_intake_form (Party A).pdf"
PDF2 = r"C:\Users\86137\Desktop\eBRAM3 - OCR成功\UC1_1_exihibit_submission (Party A).pdf"
BASE = "http://localhost:8000"

PART_A_RE = re.compile(r"===PART_A_START===(.*?)===PART_A_END===", re.DOTALL)
PART_B_RE = re.compile(r"===PART_B_START===(.*?)===PART_B_END===", re.DOTALL)

SEP  = "=" * 80
SEP2 = "-" * 60


def parse_agent_a(response):
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
            print(f"  [WARN] PART_B JSON 解析失败: {exc}")
    return part_a, part_b


def merge_part_b(part_b_list):
    merged = {}
    for page_data in part_b_list:
        if not isinstance(page_data, dict):
            continue
        for key, value in page_data.items():
            if key not in merged:
                merged[key] = value
            elif isinstance(value, list) and isinstance(merged[key], list):
                merged[key] = merged[key] + value
    return merged


def build_doc_message(doc_index, filename, summaries, merged_fields):
    filled = [(i + 1, s) for i, s in enumerate(summaries) if s.strip()]
    summary_block = "\n\n".join(f"[Page {p}]\n{t}" for p, t in filled)
    fields_json = json.dumps(merged_fields, ensure_ascii=False, indent=2)
    return (
        f"以下是材料{doc_index}《{filename}》的分析结果，"
        f"包含逐页摘要和结构化提取字段：\n\n"
        f"## Document Summary ({len(filled)} pages)\n\n"
        f"{summary_block}\n\n"
        f"---\n\n"
        f"## Structured Data\n\n"
        f"```json\n{fields_json}\n```\n"
    )


def upload_pdf(client, pdf_path, session_id, doc_label):
    print(f"\n{SEP}")
    print(f"上传 {doc_label}：{pdf_path.split(chr(92))[-1]}")
    print(f"session_id = {session_id}")
    print(SEP)

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    filename = pdf_path.split("\\")[-1]
    page_results = []
    doc_done = False

    with client.stream(
        "POST",
        f"{BASE}/pdf/pages/chat",
        data={"session_id": session_id},
        files={"pdf_file": (filename, pdf_bytes, "application/pdf")},
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

                t = ev.get("type")
                if t == "start":
                    print(f"  [start] 共 {ev.get('total_pages')} 页")
                elif t == "stage":
                    print(f"  [stage] {ev.get('message')}")
                elif t == "progress":
                    stage = ev.get("stage")
                    if stage == "ocr":
                        print(f"  [ocr] {ev.get('extracted')}/{ev.get('total')}")
                    else:
                        print(f"  [agent] 页 {ev.get('page')}/{ev.get('total')}")
                elif t == "ocr_done":
                    print(f"  [ocr_done] 成功 {ev.get('success')}，失败 {ev.get('failed')}")
                elif t == "page_result":
                    status = ev.get("status")
                    print(f"  [page_result] 第 {ev.get('page')} 页 [{status}]")
                    page_results.append(ev)
                elif t == "doc_complete":
                    print(f"  [doc_complete] {ev.get('message')}")
                    doc_done = True
                elif t == "complete":
                    print(f"  [complete] {ev.get('message')}")
                elif t == "error":
                    print(f"  [ERROR] {ev.get('message')}")

    return page_results, doc_done


def print_agent_a_report(label, page_results):
    print(f"\n{SEP}")
    print(f"AGENT A 解析报告 — {label}")
    print(SEP)

    summaries = []
    part_b_list = []

    for r in page_results:
        page = r["page"]
        status = r["status"]
        response = r.get("agent_response", "")

        print(f"\n{SEP2}")
        print(f"第 {page} 页  [status={status}]")
        print(SEP2)

        if status != "success":
            print(f"  [WARN] 失败：{r.get('error', '未知')}")
            summaries.append("")
            continue

        has_a = "===PART_A_START===" in response
        has_b = "===PART_B_START===" in response
        if not has_a and not has_b:
            print("  [WARN] 未找到 PART_A/PART_B 标记，原始回复：")
            print(f"  {response[:400]}")
            summaries.append("")
            continue

        part_a, part_b = parse_agent_a(response)

        print(f"\n  [PART A]")
        print(f"  {part_a}" if part_a else "  （空）")
        print(f"\n  [PART B]")
        if part_b is not None:
            print("  " + json.dumps(part_b, ensure_ascii=False, indent=2).replace("\n", "\n  "))
            part_b_list.append(part_b)
        else:
            print("  （解析失败）")

        summaries.append(part_a)

    merged = merge_part_b(part_b_list)
    return summaries, merged


def run():
    session_id = str(uuid.uuid4())
    print(f"\n{'#'*80}")
    print(f"  eBRAM 多文档流程测试")
    print(f"  session_id = {session_id}")
    print(f"{'#'*80}")

    with httpx.Client(timeout=600.0) as client:
        # ── 1. 上传 PDF1 ──────────────────────────────────────────────────────
        results1, ok1 = upload_pdf(client, PDF1, session_id, "材料1")
        summaries1, merged1 = print_agent_a_report("材料1", results1)
        filename1 = PDF1.split("\\")[-1]

        # ── 2. 上传 PDF2 ──────────────────────────────────────────────────────
        results2, ok2 = upload_pdf(client, PDF2, session_id, "材料2")
        summaries2, merged2 = print_agent_a_report("材料2", results2)
        filename2 = PDF2.split("\\")[-1]

    if not ok1 or not ok2:
        print("\n[ERROR] 文档处理失败，中止测试")
        return

    # ── 重建发给 Agent B 的两条消息 ───────────────────────────────────────────
    print(f"\n{SEP}")
    print("重建：发给 Agent B 的消息")
    print(SEP)

    msg1 = build_doc_message(1, filename1, summaries1, merged1)
    msg2 = build_doc_message(2, filename2, summaries2, merged2)
    final_msg = f"以上是全部 2 份材料的完整分析结果，请基于所有材料进行综合分析。"

    print(f"\n[Agent B 消息 1 — 材料1，共 {len(msg1)} 字符]\n")
    print(msg1)
    print(f"\n[Agent B 消息 2 — 材料2，共 {len(msg2)} 字符]\n")
    print(msg2)
    print(f"\n[Agent B 消息 3 — 综合分析请求]\n")
    print(final_msg)

    # ── 3. 调用 /pdf/session/analyze ─────────────────────────────────────────
    print(f"\n{SEP}")
    print("调用 /pdf/session/analyze")
    print(SEP)

    analysis_result = ""

    with httpx.Client(timeout=600.0) as client:
        with client.stream(
            "POST",
            f"{BASE}/pdf/session/analyze",
            data={"session_id": session_id},
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

                    t = ev.get("type")
                    if t == "analysis_start":
                        print(f"  [analysis_start] {ev.get('message')}")
                    elif t == "analysis_sending":
                        print(f"  [analysis_sending] {ev.get('message')}")
                    elif t == "analysis_result":
                        status = ev.get("status")
                        content = ev.get("content", "")
                        print(f"  [analysis_result] status={status}, 长度={len(content)} 字符")
                        analysis_result = content
                    elif t == "analysis_complete":
                        print(f"  [analysis_complete] {ev.get('message')}")
                    elif t == "error":
                        print(f"  [ERROR] {ev.get('message')}")

    # ── Agent B 综合分析结果 ──────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("AGENT B — 综合分析结果")
    print(SEP)
    if analysis_result:
        print(analysis_result)
    else:
        print("[WARN] 未收到 Agent B 综合分析结果")

    print(f"\n{SEP}")
    print("测试完成")
    print(SEP)


if __name__ == "__main__":
    run()
