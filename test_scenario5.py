"""
测试5专项：先文字聊天再上传PDF，验证综合分析复用同一 Agent B conversation
步骤：
  1. 发送"你好，我叫小明" → 获得 Agent B conv_id
  2. 上传 PDF → doc_complete
  3. 调用 /pdf/session/analyze 传入已有 conv_id → 验证复用
  4. 追问"我叫什么名字？" → Agent B 应回答"小明"
"""
import asyncio
import json
import sys
import io
import uuid
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE  = "http://localhost:8000"
PDF   = Path(r"C:\Users\86137\Desktop\eBRAM3 - OCR成功\eBram.pdf")


async def collect_sse(response) -> list[dict]:
    events, buf = [], ""
    async for chunk in response.aiter_text():
        buf += chunk
        parts = buf.split("\n\n")
        buf = parts.pop()
        for p in parts:
            if p.startswith("data: "):
                try:
                    events.append(json.loads(p[6:]))
                except Exception:
                    pass
    return events


async def text_chat(client, text, conversation_id=None) -> dict:
    payload = {"text": text, "user_id": "test_user"}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    r = await client.post(f"{BASE}/chat", json=payload, timeout=120)
    r.raise_for_status()
    return r.json()


def extract_reply(data: dict) -> str:
    msg = data.get("message_response", data)
    if isinstance(msg, dict):
        out = msg.get("output", [])
        if out and isinstance(out, list):
            c = out[0].get("content", {})
            if isinstance(c, dict):
                return c.get("text", "")
            return str(c)
        for k in ("answer", "text", "message", "reply"):
            if msg.get(k):
                return msg[k]
    return json.dumps(data)[:400]


async def upload_pdf(client, pdf_path: Path, session_id: str) -> list[dict]:
    with open(pdf_path, "rb") as f:
        files = {"pdf_file": (pdf_path.name, f, "application/pdf")}
        async with client.stream(
            "POST", f"{BASE}/pdf/pages/chat",
            data={"session_id": session_id},
            files=files, timeout=600,
        ) as resp:
            resp.raise_for_status()
            return await collect_sse(resp)


async def session_analyze(client, session_id: str, agent_b_conv_id: str | None = None) -> list[dict]:
    data = {"session_id": session_id}
    if agent_b_conv_id:
        data["agent_b_conversation_id"] = agent_b_conv_id
    async with client.stream(
        "POST", f"{BASE}/pdf/session/analyze",
        data=data, timeout=600,
    ) as resp:
        resp.raise_for_status()
        return await collect_sse(resp)


async def main():
    print("=" * 65)
    print("测试5：先文字聊天再上传PDF — conv_id 共享验证")
    print("=" * 65)

    session_id = str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=600) as client:

        # ── 步骤1：文字聊天"你好，我叫小明" ─────────────────────────────
        print("\n[步骤1] 发送文字：你好，我叫小明")
        resp1 = await text_chat(client, "你好，我叫小明")
        conv_id = resp1.get("conversation_id", "")
        reply1 = extract_reply(resp1)
        print(f"  Agent B conversation_id: {conv_id}")
        print(f"  Agent B 回复: {reply1[:200]}")
        assert conv_id, "❌ 未获得 conversation_id"
        print("  ✅ 步骤1通过")

        # ── 步骤2：上传 PDF ────────────────────────────────────────────
        print(f"\n[步骤2] 上传 PDF：{PDF.name}")
        evs = await upload_pdf(client, PDF, session_id)
        doc_ev = next((e for e in evs if e.get("type") == "doc_complete"), None)
        assert doc_ev, "❌ 未收到 doc_complete"
        print(f"  doc_complete: {doc_ev.get('success_pages')}/{doc_ev.get('total_pages')} 页")
        print("  ✅ 步骤2通过")

        # ── 步骤3：综合分析，传入已有 conv_id ─────────────────────────
        print(f"\n[步骤3] 综合分析（传入 agent_b_conversation_id={conv_id}）")
        evs_a = await session_analyze(client, session_id, agent_b_conv_id=conv_id)
        complete_ev = next((e for e in evs_a if e.get("type") == "analysis_complete"), {})
        result_ev   = next((e for e in evs_a if e.get("type") == "analysis_result"), {})

        returned_conv = complete_ev.get("conversation_id", "")
        analysis_ok   = result_ev.get("status") == "success"
        reused        = (returned_conv == conv_id)

        print(f"  传入  conv_id: {conv_id}")
        print(f"  返回  conv_id: {returned_conv}")
        print(f"  是否复用:      {'✅ 是' if reused else '❌ 否（新建了）'}")
        print(f"  综合分析状态:  {'✅ success' if analysis_ok else '❌ ' + result_ev.get('status','missing')}")
        print(f"  Agent B 综合回复（前400字）:\n  {result_ev.get('content','')[:400]}")

        # ── 步骤4：追问"我叫什么名字？" ──────────────────────────────
        print(f"\n[步骤4] 追问（使用 conv_id={returned_conv or conv_id}）：我叫什么名字？")
        resp4 = await text_chat(client, "我叫什么名字？",
                                conversation_id=returned_conv or conv_id)
        reply4 = extract_reply(resp4)
        conv4  = resp4.get("conversation_id", "")
        remembered = "小明" in reply4
        print(f"  Agent B 回复: {reply4[:300]}")
        print(f"  记住'小明': {'✅ 是' if remembered else '❌ 否（未记住）'}")
        print(f"  conv_id 一致: {'✅' if conv4 == (returned_conv or conv_id) else '❌'}")

        # ── 汇总 ──────────────────────────────────────────────────────
        print("\n" + "=" * 65)
        all_pass = reused and analysis_ok and remembered
        print(f"测试5 最终结果: {'✅ PASS' if all_pass else '❌ FAIL'}")
        print(f"  conv_id 复用: {'✅' if reused else '❌'}")
        print(f"  综合分析成功: {'✅' if analysis_ok else '❌'}")
        print(f"  记住'小明':   {'✅' if remembered else '❌'}")
        print("=" * 65)


asyncio.run(main())
