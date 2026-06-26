# eBRAM AI 文档助手 — 项目交接文档

> **单一权威文档**：本文件取代旧的 `project-context.md` 与 `handover-2026-05-15.md`，
> 反映 `main` 分支当前源码（commit 起点 `28eb67b`）的真实状态。
> **最近更新**：2026-06-26 ｜ **维护者**：lrn

---

## 0. 「拉下来就能跑」步骤

> 你拿到的是已清理过的 `main` 分支：`.env`、`__pycache__`、日志已不在仓库里，
> 依赖已补齐。按下面 6 步即可本地启动。

```bash
# 1. 克隆并进入项目
git clone https://github.com/NeveruaryLi/eBRAM3---OCR--.git
cd eBRAM3---OCR--

# 2. 创建 Python 3.11 环境（务必 3.11，不要用系统 3.8）
conda create -n ebram python=3.11 -y
conda activate ebram

# 3. 安装依赖（国内镜像）
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple

# 4. 安装 Playwright 浏览器内核（Case 5 爬虫需要，仅首次）
playwright install chromium

# 5. 配置密钥：复制模板后填入真实 key（见下方「需要找 lrn 要的 key」）
cp .env.example .env
#   用编辑器打开 .env，替换所有 app-xxx / xxx 占位符

# 6. 启动，浏览器访问 http://localhost:8000
python app.py
```

### 需要找索取的密钥（填进 `.env`）

| .env 变量 | 用途 | 是否必填才能启动 |
|---|---|---|
| `api_key` | Agent A（逐页摘要，Case 1/2） | ✅ 启动时校验，缺失拒绝启动 |
| `AGENT_B_API_KEY` | Agent B（Case 1 综合分析） | ✅ 启动时校验 |
| `PADDLE_OCR_TOKEN` | 飞桨 PaddleOCR 官方 API | ✅ 启动时校验 |
| `AGENT_C_API_KEY` | Agent C（Case 2 调解员简报） | ⬜ 用 Case 2 时才需要 |
| `AGENT_J_API_KEY` | Agent J（Case 5 HKLII 摘要） | ⬜ 用 Case 5 时才需要 |

> 启动校验逻辑见 [`model/config.py`](../model/config.py) 的 `validate_required_config()`：
> 仅前三项缺失会阻止启动；C/J 仅在对应 Case 被调用时生效。

### ⚠️ 安全提醒（务必知会团队）

旧提交历史中 **曾包含真实 `.env`**（已推送到 GitHub）。本次清理只是从「当前及以后的提交」
移除追踪，**历史里的旧密钥仍可被检出**。因此：

- 本次交接前后 **应轮换（重置）所有已暴露的 API key 与 OCR token**。
- 之后 `.env` 已被 `.gitignore` 忽略，不会再误提交。

### 环境注意事项

- **Windows + Word**：PDF 报告由 `docx2pdf` 把 Word 转 PDF，**Windows 上依赖本机安装的 Microsoft Word**。无 Word 时 PDF 下载会失败，可改用 Word(.docx) 下载。
- **Playwright**：`pip install` 只装 Python 包，必须再跑一次 `playwright install chromium` 下载浏览器内核。

---

## 1. 项目定位

| 项 | 值 |
|---|---|
| 名称 | eBRAM AI 文档助手（内部代号 eBRAM3） |
| 业务 | 为 eBRAM 调解/仲裁平台提供 AI 辅助文档分析 |
| 用户 | 仲裁员、调解员、律师 |
| 仓库 | https://github.com/NeveruaryLi/eBRAM3---OCR-- |
| 交付分支 | `main`（同事从此分支拉取开发） |
| 启动 | `python app.py` → http://localhost:8000 |

### 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI + uvicorn（`reload=True` 开发模式） |
| OCR | 飞桨 PaddleOCR 官方 API（轮询式，Case 1/2） |
| AI Agent | GPTBots（新加坡节点 `api-sg.gptbots.ai`，blocking 模式） |
| 前端 | 原生 JS + Server-Sent Events（SSE），无框架 |
| 报告生成 | python-docx（Word）+ docx2pdf（PDF，依赖本机 Word） |
| 爬虫 | Playwright sync API，经 `run_in_executor` 桥接 asyncio |
| PDF 处理 | PyMuPDF（`import fitz`） |

### Use Case 总览

| Case | 状态 | 一句话 | result_key |
|---|---|---|---|
| Case 1 | 🟢 已锁定 | 上传双方 PDF → Agent A 逐页摘要 → Agent B 综合 → 生成双方谈判问题清单 | `甲方` / `乙方` / `通用` |
| Case 2 | 🟢 已锁定 | 多份调解材料 PDF → Agent C 生成调解员简报 | `调解员简报` |
| Case 5 | 🟢 已验证 | 关键词 → Playwright 爬 HKLII 前 10 条 → Agent J 摘要 | `HKLII 案例摘要` |
| Case 3 / 4 / 6B | 🔴 待开发 | 见 `input_example/` 已备测试样本（仲裁/翻译/混合材料） | — |

> 注：`input_example/` 下已存在 Usecase 1/2/3A/3B/4/6B 的样本文件，3/4/6B 尚无后端实现。

---

## 2. 目录结构

```
eBRAM3---OCR--/
├── app.py                      # FastAPI 入口：挂载 router、lifespan 校验配置、页面路由
├── requirements.txt            # 依赖（含 playwright/docx2pdf/python-docx）
├── .env.example                # 密钥模板（提交）；.env 本体被 .gitignore 忽略
├── .gitignore                  # 本次新增：忽略 .env / __pycache__ / 日志等
│
├── api/                        # 路由层（HTTP 端点，不含业务逻辑）
│   ├── pdf_chat.py             # /pdf/* 路由 + 三个全局 session store 定义 + dispatch
│   ├── case5_routes.py         # /case5/scrape SSE 爬取路由（Case 5 专属）
│   ├── chat.py                 # /chat 旧端点（Agent B 文字追问）
│   └── graph_route.py          # 图表相关路由（非主路径）
│
├── cases/                      # Handler 层（每个 Case 一个文件）
│   ├── base.py                 # CaseHandler 抽象基类 + SseEvent（契约基石，勿改）
│   ├── __init__.py             # REGISTRY 注册表 + get_handler() 工厂
│   ├── case1_party_questions.py
│   ├── case2_mediator_briefing.py
│   └── case5_hklii_search.py
│
├── model/                      # 基础设施层
│   ├── config.py               # 所有 Agent key / URL 常量 / auth_headers / 启动校验
│   ├── report_generator.py     # Word/PDF 报告生成（python-docx + docx2pdf）
│   ├── pdf_processor.py        # PyMuPDF 工具（get_page_count / extract_native_text）
│   ├── utils.py                # extract_gptbots_reply() 等
│   └── schemas.py              # 共享 pydantic 模型
│
├── scraper/                    # 爬虫层（Case 5）
│   ├── __init__.py
│   └── hklii.py                # search_hklii / attach_contents_parallel + 自定义异常
│
├── static/                     # 前端（每个 Case 一套 html+js，视觉独立）
│   ├── index.html              # 首页 Case 选择卡片
│   ├── common.js               # 共用工具（parseSSE / uuid 等）
│   ├── style.css               # 全局样式（含 dark/light 双主题 CSS 变量）
│   ├── case1.html / case1.js
│   ├── case2.html / case2.js
│   ├── case5.html / case5.js
│   ├── app.js                  # 旧单页逻辑（部分被各 case.js 取代）
│   └── fonts/                  # 自托管 Outfit 字体
│
├── docs/
│   └── HANDOVER.md             # ← 本文件（唯一权威文档）
│
└── input_example/              # 各 Usecase 测试样本（PDF/图片/Excel 等）
```

---

## 3. 核心架构契约

### 3.1 CaseHandler 契约（`cases/base.py`）

所有 Case Handler 继承 `CaseHandler`，实现 4 个抽象方法：

| 方法 | 返回 | 触发端点 |
|---|---|---|
| `analyze()` | `AsyncGenerator[SseEvent, None]` | `POST /pdf/session/analyze` |
| `generate_report()` | `(file_bytes, filename, media_type)` | `POST /pdf/session/report` |
| `followup_chat()` | `{"conversation_id", "reply"}` | `POST /pdf/session/chat` |
| `get_downloadable_keys()` | `list[str]` | 路由层下载前验证合法性 |

> ⚠️ **返回顺序以实现为准**：`generate_report()` 实际返回 `(file_bytes, filename, media_type)`
> （见 `case1` 第 280 行与 `pdf_chat.py:1103` 的解包）。`base.py` docstring 写成
> `(bytes, media_type, filename)` 是 **过时的笔误**，但 base.py 标注"不再修改"，
> 故以代码实际顺序为准。

**SseEvent 三个合法 type**（Handler 产出）：

```python
SseEvent(type="progress", data={"message": str, ...})       # 进度，流继续
SseEvent(type="result",   data={"result_key": str, ...})    # 一个结果完成，前端启用下载
SseEvent(type="error",    data={"message": str, ...})       # 局部错误，流继续
# 致命错误 → raise，路由层捕获后发 fatal_error 并关流
```

**致命 vs 局部错误判断**：若错误导致所有剩余 result_key 都无法完成 → `raise`（致命）；
否则 `yield SseEvent(type="error")` 后继续（局部）。

**注册新 Case**（`cases/__init__.py`）：

```python
REGISTRY: dict[str, type[CaseHandler]] = {
    "case1": Case1Handler,
    "case2": Case2MediatorBriefingHandler,
    "case5": Case5HkliiSearchHandler,
    # 新 Case 在此登记 case_type 字符串 → Handler 类
}
```

### 3.2 Session 数据流（`api/pdf_chat.py`）

三个全局内存 dict（服务重启即清空）：

| Store | 内容 |
|---|---|
| `session_store` | Case 1/2：`list[DocResult]`（Agent A 结果）；Case 5：`list[ScrapedResult]` |
| `session_metadata` | `{"case_type": "case1"\|"case2"\|"case5", ...}` — dispatch 依据 |
| `session_results_store` | Handler 写入的最终结果：`{sid: {"case_type", "created_at", "results": {result_key: {...}}}}` |

**Dispatch**：`_get_handler(sid)` 先查 `session_metadata[sid]["case_type"]`，回退到
`session_results_store`，再回退默认 `"case1"`（过渡期 fallback，引入持久化后移除）。

**写入时机**：
- Case 1/2：`POST /pdf/pages/chat` 逐份上传，首次创建 session 时写 `session_metadata`。
- Case 5：`POST /case5/scrape` 完成时写 `session_store` + `session_metadata`（`case_type="case5"`）。
- 所有 Case：`analyze()` 内写 `session_results_store`，再 yield `result` 事件。

**TTL 清理**：后台任务每 30 分钟扫描，删除 2 小时（`SESSION_TTL_SECONDS=7200`）以上的会话。
对无 `created_at` 的对象（如 `ScrapedResult`）用 `hasattr` 跳过，避免 `AttributeError`。

### 3.3 路由总览

| 端点 | 方法 | 说明 |
|---|---|---|
| `/` `/case1` `/case2` `/case5` | GET | 返回对应 HTML 页面 |
| `/pdf/pages/chat` | POST | 上传 PDF → OCR → Agent A 逐页分析（SSE）；单/多文档由 `session_id` 决定 |
| `/pdf/session/analyze` | POST | 触发 Handler.analyze()（SSE，所有 Case 共用，按 case_type dispatch） |
| `/pdf/session/report` | POST | 下载报告（Word/PDF，所有 Case 共用） |
| `/pdf/session/chat` | POST | 文字追问（所有 Case 共用） |
| `/pdf/chat` | POST | 旧端点：整份 PDF 一次性 OCR+分析 |
| `/case5/scrape` | POST | Case 5 专属：HKLII 爬取（SSE） |

### 3.4 GPTBots 调用约定（决策 D1，全 Case 共用）

```python
payload = {
    "conversation_id": conv_id,
    "response_mode": "blocking",
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": text_prompt},
            {"type": "document", "document": [doc_item]},  # ⚠️ document 是「列表」
        ],
    }],
}
```

> **核心坑**：`"document": [doc_item]` 必须是数组。写成对象会被 Agent 静默忽略附件，只回文字。
> `doc_item` 形如 `{"base64_content": <b64>, "format": "md", "name": "xxx.md"}`。

**Agent 与配置对应**（`model/config.py`，命名规范 `agent_{x}_auth_headers()`）：

| Agent | key 变量 | 职责 | Case |
|---|---|---|---|
| Agent A | `api_key` | 逐页 PDF 摘要（输出 PART_A 摘要 + PART_B JSON 字段） | 1 / 2 |
| Agent B | `AGENT_B_API_KEY` | Case 1 综合分析，生成问题清单 | 1 |
| Agent C | `AGENT_C_API_KEY` | Case 2 调解员简报 | 2 |
| Agent J | `AGENT_J_API_KEY` | Case 5 HKLII 案例摘要 | 5 |

> Agent A 回复用 `===PART_A_START===…===PART_A_END===`（摘要）和
> `===PART_B_START===…===PART_B_END===`（JSON 字段）分段，由 `_parse_agent_a_response()` 解析。

---

## 4. 各 Case 实现要点

### Case 1 — 双方谈判问题生成 🟢
- 文件：`cases/case1_party_questions.py` ｜ `static/case1.{html,js}`
- 甲乙方各自独立上传（`party` 字段区分），各自独立 conversation，串行处理。
- 每方一份 result：`result_key = "甲方" / "乙方"`（单方上传时为 `通用`）。
- 报告：Word 优先 + PDF，文件名如 `10 Questions for Party A.pdf`。

### Case 2 — 调解员简报 🟢
- 文件：`cases/case2_mediator_briefing.py` ｜ `static/case2.{html,js}`
- 多份材料混合上传，无甲乙方区分；Agent C 输出 Markdown 简报。
- 单一 result：`result_key = "调解员简报"`，前端用 `marked.js` 渲染预览。

### Case 5 — HKLII 案例检索 🟢
- 文件：`scraper/hklii.py` ｜ `api/case5_routes.py` ｜ `cases/case5_hklii_search.py` ｜ `static/case5.{html,js}`
- 两段式：`/case5/scrape`（Playwright 抓取）→ `/pdf/session/analyze`（Agent J 摘要）。
- 单一 result：`result_key = "HKLII 案例摘要"`，followup 复用 analyze 的 conversation_id（保留 Agent J 上下文）。
- 边界：`NoResultsFound` → `no_results` 事件；`SearchTimeout` → `search_timeout` 事件。
- 关键阈值：GOTO 60s；无结果 selector `tr.v-data-table__empty-wrapper`（与 `tr.resultrow` 互斥等待）。

---

## 5. 开发经验与陷阱（务必先读）

### 前端
- **JS 模板字符串里的反斜杠会被静默吞掉**：`` `\pdf\session\analyze` `` 实际变成 `pdfsessionanalyze`。URL 路径一律用正斜杠。
- **复制已有 case.js 改造时必须全量审查**：端点 URL、DOM id、状态变量名（如 `scrapedResults` vs `fileQueue`）都要逐一改，漏改会导致功能串台（追问打到错误 Agent）。
- **折叠/重置状态要在 `resetCurrentView()` 统一复位**，否则界面残留。

### 后端
- **GPTBots `document` 必须是列表**（见 3.4）。
- **Playwright sync API 不能在 asyncio 直接调用**：必须 `loop.run_in_executor(executor, fn, ...)`；executor 用全局单例 `ThreadPoolExecutor`。
- **Session TTL 清理用 `hasattr` 检查 `created_at`**（`ScrapedResult` 没有该属性）。
- **`docx2pdf` 依赖本机 Word**（Windows）；CI/无 Word 环境下 PDF 会失败。
- **超时留余量**：HKLII 国际访问较慢，等待结果 25–30s、GOTO 60s。

### 调试方法论
- 排查 DOM 时序用 `headless=False` + DevTools 实际观察，别只靠截图。
- 用多个互斥 selector 等待替代单一长超时（避免无结果时傻等）。
- 先 grep 全量确认影响范围，再动代码。

---

## 6. 仓库卫生状态（本次交接已处理）

| 项 | 处理 |
|---|---|
| `.gitignore` | ✅ 新增（忽略 `.env` / `__pycache__` / `*.log` / playwright 产物等） |
| `.env` | ✅ 从追踪移除（本地仍保留）；**历史仍含旧密钥 → 需轮换** |
| `__pycache__/*.pyc`、`server*.log`、`server_err/log.txt` | ✅ 从追踪移除 |
| 双 README（`README.MD` 大小写冲突） | ✅ 删除大写版，保留 `README.md` |
| `requirements.txt` | ✅ 补齐 `python-docx` / `docx2pdf` / `playwright` |
| 旧文档 `project-context.md` / `handover-2026-05-15.md` | ✅ 删除，合并进本文件 |

> `input_example/` 内大量二进制样本仍在仓库中（克隆体积偏大）。如需瘦身可后续移到
> Git LFS 或单独的样本仓库——本次未动，避免影响现有测试。

---

## 7. 待办 / 下一步

- **Case 3 / 4 / 6B**：`input_example/` 已有样本，后端待实现。新增时遵循 §3.1 契约 +
  在 `REGISTRY` 注册 + 新增 `static/caseX.{html,js}` + `app.py` 加页面路由。
- **轮换已暴露密钥**（见 §0 安全提醒）。
- **持久化**：当前 session 全内存，重启即失。引入持久化时移除 `_get_handler` 的 `"case1"` fallback。
```
