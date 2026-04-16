"""
通用工具函数

extract_gptbots_reply: 从 GPTBots 任意格式的响应中提取纯文本，
兼容新旧格式：
  - {"output": [{"content": {"text": "..."}}]}   新格式
  - {"answer": "..."}  {"text": "..."}            旧格式
  - {"data": {"answer": "..."}}                   data 嵌套
"""


def extract_gptbots_reply(data: object) -> str:
    """从 GPTBots 任意格式的响应中提取文本。"""
    if not isinstance(data, dict):
        return str(data) if data is not None else ""

    # output 列表格式（新格式）
    output = data.get("output")
    if isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, dict):
            content = first.get("content")
            if isinstance(content, dict):
                text = content.get("text")
                if text and isinstance(text, str):
                    return text
            elif isinstance(content, str) and content:
                return content

    # 常规字符串字段
    for key in ("answer", "text", "content", "message", "reply"):
        val = data.get(key)
        if val and isinstance(val, str):
            return val

    # data 嵌套结构（递归）
    inner = data.get("data")
    if isinstance(inner, dict):
        return extract_gptbots_reply(inner)

    return str(data)
