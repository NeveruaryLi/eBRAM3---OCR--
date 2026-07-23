import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    """读取环境变量并去除首尾空白，空值回落到默认值。"""
    return (os.getenv(name) or "").strip() or default


# ── GPTBots 服务配置 ──────────────────────────────────────────────────────────
API_KEY: str = _env("api_key") or _env("API_KEY")
AGENT_B_API_KEY: str = _env("AGENT_B_API_KEY")
AGENT_C_API_KEY: str = _env("AGENT_C_API_KEY")   # Case 2 调解员简报 Agent
AGENT_J_API_KEY: str = _env("AGENT_J_API_KEY")   # Case 5 HKLII 摘要 Agent
AGENT_I_API_KEY: str = _env("AGENT_I_API_KEY")   # Case 4 PDF 图片翻译 Agent
AGENT_K_API_KEY: str = _env("AGENT_K_API_KEY")   # Case 6A 官网服务指导 Agent
AGENT_L_API_KEY: str = _env("AGENT_L_API_KEY")   # Case 6B 服务协议草案 Agent

MESSAGE_URL: str = _env(
    "base_url",
    "https://api-sg.gptbots.ai/v2/conversation/message",
)

CREATE_URL: str = _env(
    "create_conversation_url",
    "https://api-sg.gptbots.ai/v1/conversation",
)

MESSAGES_URL: str = _env(
    "messages_url",
    "https://api-sg.gptbots.ai/v2/messages",
)

DEFAULT_USER_ID: str = _env("gptbots_user_id", "local_user")

# ── PaddleOCR 官方 API 配置 ──────────────────────────────────────────────────
PADDLE_OCR_TOKEN: str = _env("PADDLE_OCR_TOKEN")
PADDLE_OCR_JOB_URL: str = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
PADDLE_OCR_MODEL: str = "PaddleOCR-VL-1.5"


def validate_required_config() -> None:
    """
    在应用启动时调用，检查所有必需的环境变量。
    任何一项缺失都会抛出 RuntimeError，阻止服务启动。
    """
    if not API_KEY:
        raise RuntimeError(
            "缺少必需的环境变量 api_key（Agent A GPTBots API 密钥），请检查 .env 文件"
        )
    if not AGENT_B_API_KEY:
        raise RuntimeError(
            "缺少必需的环境变量 AGENT_B_API_KEY（Agent B GPTBots API 密钥），请检查 .env 文件"
        )
    if not PADDLE_OCR_TOKEN:
        raise RuntimeError(
            "缺少必需的环境变量 PADDLE_OCR_TOKEN（飞桨 PaddleOCR API 令牌），请检查 .env 文件"
        )
    logger.info(
        "配置验证通过：Agent A/B GPTBots 密钥及 PaddleOCR 密钥已配置，MESSAGE_URL=%s，OCR_MODEL=%s",
        MESSAGE_URL,
        PADDLE_OCR_MODEL,
    )


def auth_headers() -> dict[str, str]:
    """构造调用 Agent A GPTBots API 所需的请求头。"""
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def agent_b_auth_headers() -> dict[str, str]:
    """构造调用 Agent B GPTBots API 所需的请求头。"""
    return {
        "Authorization": f"Bearer {AGENT_B_API_KEY}",
        "Content-Type": "application/json",
    }


def agent_c_auth_headers() -> dict[str, str]:
    """构造调用 Agent C GPTBots API 所需的请求头（Case 2 调解员简报）。"""
    return {
        "Authorization": f"Bearer {AGENT_C_API_KEY}",
        "Content-Type": "application/json",
    }


def agent_j_auth_headers() -> dict[str, str]:
    """构造调用 Agent J GPTBots API 所需的请求头（Case 5 HKLII 摘要）。"""
    return {
        "Authorization": f"Bearer {AGENT_J_API_KEY}",
        "Content-Type": "application/json",
    }


def agent_i_auth_headers() -> dict[str, str]:
    """构造调用 Agent I（Case 4 PDF 图片翻译）所需的请求头。"""
    return {
        "Authorization": f"Bearer {AGENT_I_API_KEY}",
        "Content-Type": "application/json",
    }


def agent_k_auth_headers() -> dict[str, str]:
    """构造调用 Agent K（Case 6A 官网服务指导）所需的请求头。"""
    return {
        "Authorization": f"Bearer {AGENT_K_API_KEY}",
        "Content-Type": "application/json",
    }


def agent_l_auth_headers() -> dict[str, str]:
    """构造调用 Agent L（Case 6B 服务协议草案）所需的请求头。"""
    return {
        "Authorization": f"Bearer {AGENT_L_API_KEY}",
        "Content-Type": "application/json",
    }


def paddle_ocr_headers() -> dict[str, str]:
    """构造调用飞桨 PaddleOCR 官方 API 所需的请求头。"""
    return {"Authorization": f"bearer {PADDLE_OCR_TOKEN}"}


def pick_conversation_id(data: dict) -> str | None:
    """
    从 GPTBots 创建对话的响应中提取 conversation_id。
    兼容顶层和 data 嵌套两种结构。
    """
    if not isinstance(data, dict):
        return None
    cid = data.get("conversation_id")
    if cid:
        return str(cid)
    inner = data.get("data")
    if isinstance(inner, dict):
        cid = inner.get("conversation_id")
        if cid:
            return str(cid)
    return None
