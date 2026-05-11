"""
cases/__init__.py — Case 注册表 + Handler 工厂函数

新增 Case 时只需：
    1. 在 cases/ 下新建实现文件（如 case2_mediator_briefing.py）
    2. 在 REGISTRY 中注册对应的 case_type 字符串
路由层通过 get_handler(case_type) 获取 Handler，不感知具体实现类。

待办：
    S10 完成 Case1Handler 实现后，在 REGISTRY 中注册 "case1"。
"""
from __future__ import annotations

from cases.base import CaseHandler

# ── Case 注册表 ───────────────────────────────────────────────────────────────
# key:   case_type 字符串，与 session_metadata["case_type"] 及前端传参保持一致
# value: CaseHandler 的具体实现类（未实例化）
#
# TODO(S10): 注册 Case1Handler
#   from cases.case1_party_questions import Case1Handler
#   REGISTRY["case1"] = Case1Handler
#
# TODO(Case 2): 注册 Case2Handler
#   from cases.case2_mediator_briefing import Case2Handler
#   REGISTRY["case2"] = Case2Handler

REGISTRY: dict[str, type[CaseHandler]] = {
    # 待 S10 填入
}


def get_handler(case_type: str) -> CaseHandler:
    """
    工厂函数：根据 case_type 字符串返回对应 CaseHandler 实例。

    Args:
        case_type: 与 session_metadata["case_type"] 一致的字符串，如 "case1"、"case2"。

    Returns:
        对应 CaseHandler 的实例（无状态，每次调用新建）。

    Raises:
        ValueError: case_type 未注册时抛出，路由层应转换为 HTTP 400。
    """
    handler_cls = REGISTRY.get(case_type)
    if handler_cls is None:
        registered = list(REGISTRY.keys())
        raise ValueError(
            f"未知的 case_type: {case_type!r}。"
            f"已注册的类型：{registered}。"
            f"请在 cases/__init__.py 的 REGISTRY 中注册该类型。"
        )
    return handler_cls()
