"""
scraper/hklii.py
~~~~~~~~~~~~~~~~
HKLII（香港法律信息研究所）Playwright 爬取模块。

提供两个主要函数：
- search_hklii()           — 搜索并返回前 N 条结果的元数据
- attach_contents_parallel() — 并行抓取每条结果的正文，写入 ScrapedResult.content

内部辅助函数（可单独测试）：
- absolute_hklii_url() / clean_content() / get_page_content()

设计说明
--------
- 内容截断是 handler 层（case5_hklii_search.py）的职责，本模块只返回完整原始正文。
- 乱码检测也在 handler 层处理，本模块不做过滤。
- 本模块的所有函数使用 Playwright sync API，调用方需在 asyncio 环境中
  通过 loop.run_in_executor(executor, ...) 执行，避免阻塞事件循环。
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

logger = logging.getLogger(__name__)

HKLII_ORIGIN = "https://www.hklii.hk"

# 并行抓取最大线程数上限（接口层可再收紧）
CONTENT_CONCURRENCY_CAP = 32

# 等待策略：domcontentloaded 比 load 更快；部分站点资源很慢会导致 load 超时
GOTO_WAIT = "domcontentloaded"
GOTO_TIMEOUT_MS = 60_000  # 60s：超过此时间视为网络异常

# HKLII Vuetify 数据表：无结果时渲染的特殊行（与 tr.resultrow 互斥）
# 观察于 2026-05-15，通过 headless=True + 垃圾关键词确认
_NO_RESULTS_SELECTOR = "tr.v-data-table__empty-wrapper"


# ---------------------------------------------------------------------------
# 自定义异常（供上层路由层区分处理）
# ---------------------------------------------------------------------------


class NoResultsFound(Exception):
    """HKLII 搜索成功但无匹配案例（关键词过于冷僻或拼写错误）。"""


class SearchTimeout(Exception):
    """HKLII 搜索超时或网络异常，无法在预期时间内获取结果。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class ScrapedResult:
    """一条 HKLII 搜索结果的完整数据。

    Attributes:
        title:    判决书/法规标题
        doc_type: 文档类型标签，如 'case'、'legis'（来自 .resulttype 元素）
        database: 数据库/法院名称，如 'Court of First Instance'
        subtitle: 副标题，通常是案件编号或法规编号（用作 Citation）
        date:     日期字符串，如 '27 Mar, 2025'
        url:      完整 HKLII URL（已转换为绝对路径）
        content:  抓取到的正文文本（attach_contents_parallel 调用前为空字符串）
        keyword:  触发本次搜索的关键词（溯源用）
    """

    title: str
    doc_type: str
    database: str
    subtitle: str
    date: str
    url: str
    content: str = field(default="")
    keyword: str = field(default="")


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def absolute_hklii_url(href: str) -> str:
    """将搜索结果里的相对路径转换为绝对 HKLII URL。"""
    if not href:
        return ""
    return urljoin(HKLII_ORIGIN.rstrip("/") + "/", href)


def clean_content(content: str) -> str:
    """清理正文：去除每行首尾空白，压缩空行，保留段落结构。"""
    if not content:
        return ""
    lines = [line.strip() for line in content.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def get_page_content(url: str, *, headless: bool = True) -> str:
    """
    用 Playwright 获取单个页面的正文内容。

    使用 CSS selector 瀑布：.case-content → 其他常见 selector → 空字符串兜底。
    正文字符 ≤ 100 时视为无效（如仅有 nav/header），返回空字符串。

    :param url:      完整 HKLII URL
    :param headless: 是否无头模式（生产环境应为 True）
    :return:         清理后的正文文本
    """
    content = ""
    if not url:
        return content

    # 确保是绝对 URL
    if not url.startswith("http"):
        url = absolute_hklii_url(url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36"
            )
        )
        try:
            page.goto(url, wait_until=GOTO_WAIT, timeout=GOTO_TIMEOUT_MS)

            # 等待内容区域就绪（超时不报错，继续尝试抓取）
            try:
                page.wait_for_selector(
                    ".case-content, .judgment-content, .document-content, "
                    ".legis-content, article, .content-body, #doc-content",
                    timeout=30_000,
                )
            except Exception:
                pass

            # 主 selector：.case-content（案例页面最常见）
            case_elem = page.query_selector(".case-content")
            if case_elem:
                content = case_elem.inner_text().strip()

            # Fallback selector 瀑布
            if not content:
                fallbacks = [
                    ".judgment-content",
                    ".document-content",
                    ".legis-content",
                    "article",
                    ".content-body",
                    "#doc-content",
                ]
                for selector in fallbacks:
                    elem = page.query_selector(selector)
                    if elem:
                        text = elem.inner_text().strip()
                        if len(text) > 100:
                            content = text
                            break

        except Exception as exc:
            logger.warning("get_page_content 错误 [%s]: %s", url, exc)
        finally:
            browser.close()

    return content


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def search_hklii(
    keyword: str,
    *,
    max_results: int = 10,
    headless: bool = True,
) -> list[ScrapedResult]:
    """
    搜索 HKLII 并返回前 max_results 条结果的元数据列表。

    返回的 ScrapedResult.content 均为空字符串，需调用 attach_contents_parallel()
    后才会填充正文。

    :param keyword:     搜索关键词（建议使用英文，HKLII 对中英文均有索引）
    :param max_results: 最多返回条数（默认 10）
    :param headless:    是否无头模式（默认 True）
    :return:            ScrapedResult 列表（顺序与搜索结果页一致）
    """
    results: list[ScrapedResult] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36"
            )
        )
        try:
            search_url = f"https://www.hklii.hk/search?q={keyword}"
            logger.info("HKLII 搜索：%s", search_url)
            page.goto(search_url, wait_until=GOTO_WAIT, timeout=GOTO_TIMEOUT_MS)

            # 等待真实结果出现（HKLII 是 Vuetify SPA：骨架先渲染空表，数据异步填入）
            # 注意：_NO_RESULTS_SELECTOR 在页面骨架阶段就会出现，不能作为联合等待条件，
            # 否则有结果的搜索也会立即命中空表骨架而误判为无结果。
            # 正确策略：只等 resultrow，超时后再检查是否真的无结果。
            try:
                page.wait_for_selector("tr.resultrow", timeout=15_000)
            except PlaywrightTimeoutError:
                # 15s 内 resultrow 未出现——判断是"无结果"还是"网络超时"
                if page.query_selector(_NO_RESULTS_SELECTOR):
                    raise NoResultsFound(
                        f"HKLII 未找到关键词 \"{keyword}\" 的相关案例，请尝试其他关键词"
                    )
                raise SearchTimeout(
                    "HKLII 搜索结果未在 15 秒内出现，页面可能无法访问，请稍后重试"
                )

            rows = page.query_selector_all("tr.resultrow")
            logger.info("HKLII 找到 %d 条结果行，取前 %d 条", len(rows), max_results)

            for row in rows[:max_results]:
                try:
                    link_elem = row.query_selector("a.routing")
                    raw_href = link_elem.get_attribute("href") if link_elem else ""
                    url = absolute_hklii_url(raw_href)

                    type_elem = row.query_selector(".resulttype")
                    doc_type = type_elem.inner_text().strip() if type_elem else ""

                    db_elem = row.query_selector("td:nth-child(2) .resultcontent")
                    database = db_elem.inner_text().strip() if db_elem else ""

                    title_elem = row.query_selector(".resulttitle")
                    title = title_elem.inner_text().strip() if title_elem else ""

                    subtitle_elem = row.query_selector(
                        "td:nth-child(3) .darkgrey--text"
                    )
                    subtitle = (
                        subtitle_elem.inner_text().strip() if subtitle_elem else ""
                    )

                    date_elem = row.query_selector(
                        "td:nth-child(4) .resultcontent"
                    )
                    date = date_elem.inner_text().strip() if date_elem else ""

                    results.append(
                        ScrapedResult(
                            title=title,
                            doc_type=doc_type,
                            database=database,
                            subtitle=subtitle,
                            date=date,
                            url=url,
                            content="",
                            keyword=keyword,
                        )
                    )
                except Exception as exc:
                    logger.warning("HKLII 解析结果行错误: %s", exc)

        except (NoResultsFound, SearchTimeout):
            raise  # 透传给上层处理，不视为通用错误
        except Exception as exc:
            logger.error("HKLII 搜索失败: %s", exc)
            raise SearchTimeout(f"HKLII 搜索异常：{exc}") from exc
        finally:
            browser.close()

    logger.info("HKLII 搜索完成：返回 %d 条 ScrapedResult", len(results))
    return results


def attach_contents_parallel(
    results: list[ScrapedResult],
    *,
    content_concurrency: int = 3,
    headless: bool = True,
) -> None:
    """
    就地并行抓取每条结果的正文，写入 ScrapedResult.content。

    使用 ThreadPoolExecutor，每条任务独立启动一个 Playwright 浏览器实例。
    顺序与 results 列表保持一致。

    :param results:             search_hklii() 返回的列表（就地修改）
    :param content_concurrency: 最大并发线程数（默认 3，受 CONTENT_CONCURRENCY_CAP 限制）
    :param headless:            是否无头模式（默认 True）
    """
    if not results:
        return

    workers = min(
        max(1, int(content_concurrency)),
        CONTENT_CONCURRENCY_CAP,
        len(results),
    )
    logger.info(
        "attach_contents_parallel：%d 条结果，%d 线程并发", len(results), workers
    )

    def fetch_one(url: str) -> str:
        return clean_content(get_page_content(url, headless=headless))

    urls = [r.url for r in results]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        contents = list(pool.map(fetch_one, urls))

    for result, text in zip(results, contents):
        result.content = text

    logger.info("attach_contents_parallel 完成，内容字符数：%s", [len(r.content) for r in results])
