"""
cases/base.py — CaseHandler 抽象基类 + SseEvent 数据类

所有 Case Handler 必须继承 CaseHandler 并实现全部四个抽象方法。
本文件是 7 个 Case 的契约基石，内容经确认后不再修改。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncGenerator


@dataclass
class SseEvent:
    """
    Handler 产出的 SSE 事件单元。

    type — 路由层可感知的三个合法值：
        "progress"  中间进度提示，前端更新进度展示
        "result"    一个结果单元处理完成，前端据 data["result_key"] 启用下载按钮
        "error"     局部错误，流继续；路由层仅记录日志

    data — Handler 完全自治：
        路由层将整个 dict 原样序列化为 JSON 透传给前端（spread 方式）：
            yield _sse({"type": event.type, **event.data})
        路由层不读取、不修改 data 中任何字段（含 result_key）。
        前端按 (case_type, type) 元组决定渲染逻辑。
        各 Case 的 data schema 互不约束，由 Handler 实现类的
        docstring 记录本 Case 使用的具体字段。

    设计约定（路由层事件 vs Handler 事件）：
        路由层发送（不经过本类）：analysis_start / analysis_complete / error
        Handler 产出（本类实例）：progress / result / error
    """
    type: str            # "progress" | "result" | "error"
    data: dict[str, Any]


class CaseHandler(ABC):
    """
    所有 Case Handler 的抽象基类。

    职责划分：
        Handler  — 调用 Agent、产出 SseEvent、写入 session_results_store
        路由层   — SSE 序列化、HTTP 响应头、流的开启/关闭、顶层异常捕获

    ── 致命 vs 局部错误判断准则（Handler 实现者必须遵循）────────────────

    判断问题：该错误是否导致所有剩余 result_key 的处理都不可能完成？

        → 是（致命）：raise 异常
            路由层捕获后发 {"type": "error", "message": ...} 并关闭流。
            典型场景：
              · session_store / session_results_store 中 session_id 不存在
              · API Key 未配置，所有 Agent 调用都将失败
              · 写入 session_results_store 失败（引入持久化后）

        → 否（局部）：yield SseEvent(type="error", data={...})，继续处理下一个 result_key
            典型场景：
              · 外部 Agent 调用失败（HTTP 4xx/5xx、超时）
              · Agent 返回空字符串或响应格式非预期
              · 单个 result_key 处理结果无效

    ────────────────────────────────────────────────────────────────────────
    """

    @abstractmethod
    async def analyze(
        self,
        session_id: str,
        session_store: dict[str, Any],
        session_results_store: dict[str, Any],
    ) -> AsyncGenerator[SseEvent, None]:
        """
        综合分析 session 内所有文档，逐步产出 SseEvent。

        路由层在调用前后负责发送（不在 Handler 里）：
            {"type": "analysis_start",    ...}  # async for 启动前
            {"type": "analysis_complete", ...}  # generator 正常耗尽后
            {"type": "error",             ...}  # 未捕获异常逃逸时

        Handler 产出的事件约定：

            SseEvent(type="progress", data={"message": str, ...})
                进行中的进度提示，前端更新 UI。

            SseEvent(type="result", data={"result_key": str, ...})
                某 result_key 分析完成。
                · result_key 的值必须与 get_downloadable_keys() 返回值一致
                · 路由层 spread 序列化后前端读 result_key 启用下载按钮
                · Handler 必须在产出此事件前完成写入 session_results_store

            SseEvent(type="error", data={"message": str, ...})
                局部错误，流继续。
                · 遵循类 docstring 中的致命/局部判断准则
                · 不可 raise，由 Handler 决定是否继续处理其余 result_key

        副作用（写入时机）：
            session_results_store[session_id]["results"][result_key] 的写入
            必须在对应 SseEvent(type="result") 产出之前完成，
            确保前端收到事件后立即可以发起报告下载请求。
        """
        ...

    @abstractmethod
    async def generate_report(
        self,
        session_id: str,
        result_key: str,
        output_format: str,                       # "pdf" | "docx"
        session_results_store: dict[str, Any],
    ) -> tuple[bytes, str, str]:
        """
        生成并返回报告文件。

        Args:
            result_key: 必须是 get_downloadable_keys() 返回列表中的合法值。
                        路由层在调用此方法前应先调用 get_downloadable_keys()
                        验证合法性，非法值返回 HTTP 400，不进入此方法。
                        Case 1: "甲方" | "乙方"
                        Case 2: "briefing"
            output_format: "pdf"（优先）或 "docx"（降级或显式请求）

        Returns:
            (file_bytes, media_type, download_filename)
            例：(b"...", "application/pdf", "10 Questions for Party A.pdf")
        """
        ...

    @abstractmethod
    async def followup_chat(
        self,
        session_id: str,
        text: str,
        conversation_id: str | None,
        session_results_store: dict[str, Any],
    ) -> dict[str, Any]:
        """
        文字追问，返回 Agent 回复。

        Args:
            conversation_id:
                None  — 首次追问，Handler 内部创建新对话并注入分析结果作为上下文
                有值  — 复用已有对话，Handler 直接发送消息，不重新注入上下文

        Returns:
            {"conversation_id": str, "reply": str}
            路由层将 conversation_id 返回给前端，前端下次追问时带上。
        """
        ...

    @abstractmethod
    def get_downloadable_keys(
        self,
        session_id: str,
        session_results_store: dict[str, Any],
    ) -> list[str]:
        """
        返回当前 session 实际可下载的报告键列表。

        调用时机（服务端路由层内部调用，不对应任何前端 HTTP 请求）：
            路由层收到报告下载请求时调用，用于验证 result_key 合法性。
            合法 → 调 generate_report()；非法 → 返回 HTTP 400。

        前端如何知道哪些按钮可用（无需调用此方法）：
            前端在 analyze 流中收到 SseEvent(type="result") 时，
            实时读 result_key 并直接启用对应下载按钮（渐进式）。

        仅返回已成功写入 session_results_store 的键；
        分析失败的 result_key 不包含在返回列表中。

        同步方法（当前内存查询足够，引入持久化时改为 async）。

        Case 1 示例：["甲方", "乙方"]（乙方失败则仅 ["甲方"]）
        Case 2 示例：["briefing"]
        """
        ...
