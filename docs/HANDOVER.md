# eBRAM AI 文档助手 — 项目交接文档

> **单一权威文档**：本文档反映 main @ a0b0c346、标签 case4-case5-v1.0
> 的真实代码状态。Case 4 与 Case 5 已包含在该版本中。
> **最近更新**：2026-07-22 ｜ **维护方式**：功能合并后随代码同步更新

---

## 0. 拉取与启动

仓库目前为 **Private**。新成员必须先由仓库管理员在 GitHub 添加为 Collaborator，
并通过 Git Credential Manager、GitHub CLI 或 SSH 完成身份验证。

~~~text
# 1. 克隆并进入项目
git clone https://github.com/NeveruaryLi/eBRAM3---OCR--.git
cd eBRAM3---OCR--

# 2. 创建 Python 3.11 环境（不要使用旧的系统 Python 3.8）
conda create -n ebram python=3.11 -y
conda activate ebram

# 3. 安装 Python 依赖
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple

# 4. 安装 Case 5 所需的 Chromium（仅首次）
playwright install chromium

# 5. 从模板创建本地配置
# Windows PowerShell
Copy-Item .env.example .env
# macOS / Linux
cp .env.example .env

# 6. 编辑 .env 后启动
python app.py
~~~

浏览器入口：<http://localhost:8000>；OpenAPI：<http://localhost:8000/docs>。

### 环境变量

| 变量 | 用途 | 缺失时行为 |
|---|---|---|
| api_key | Agent A：Case 1/2 逐页提取 | 阻止服务启动 |
| AGENT_B_API_KEY | Agent B：Case 1 综合分析 | 阻止服务启动 |
| PADDLE_OCR_TOKEN | PaddleOCR：Case 1/2 文档 OCR | 阻止服务启动 |
| AGENT_C_API_KEY | Agent C：Case 2 调解员简报 | Case 2 调用失败，不影响启动 |
| AGENT_I_API_KEY | Agent I：Case 4 页面图片翻译 | Case 4 明确提示不可用，不影响启动 |
| AGENT_J_API_KEY | Agent J：Case 5 HKLII 摘要 | Case 5 调用失败，不影响启动 |
| base_url | GPTBots blocking 消息接口 | 默认新加坡节点 |
| create_conversation_url | GPTBots 创建对话接口 | 默认新加坡节点 |
| messages_url | GPTBots 会话详情接口，供 Case 4 取译图 | 默认新加坡节点 |
| gptbots_user_id | GPTBots 默认用户标识 | 默认 local_user |

真实密钥只保存在本机 .env 或受控 Secret Manager 中；GitHub Actions 使用
Repository Secrets。不要把密钥写入代码、提交、Issue、PR 或聊天记录。

### 安全状态

旧公开提交历史曾包含真实 .env。仓库改为 Private 只限制后续访问，不能撤销已经被
复制的凭证；截至本基线，Agent A、Agent B 与 PaddleOCR 的旧凭证尚未完成轮换。
这些凭证应视为已暴露，出现异常调用时必须立即撤销，长期建议仍是尽快全部轮换。

### 环境注意事项

- Case 1/2/5 的 PDF 报告由 docx2pdf 转换，Windows 需要安装 Microsoft Word；无 Word
  时可下载 DOCX。Case 4 直接用 PyMuPDF 生成 PDF，不依赖 Word。
- pip install playwright 不包含浏览器内核，Case 5 首次运行前必须执行
  playwright install chromium。
- 当前服务无鉴权，默认只应在可信内网或本机环境运行。

---

## 1. 项目定位与版本状态

| 项 | 值 |
|---|---|
| 名称 | eBRAM AI 文档助手（内部代号 eBRAM3） |
| 业务 | 为调解、仲裁与法律文档工作流提供 AI 辅助处理 |
| 用户 | 仲裁员、调解员、律师及内部运营人员 |
| 仓库 | NeveruaryLi/eBRAM3---OCR--（Private） |
| 稳定分支 | main |
| 当前版本标签 | case4-case5-v1.0 |
| 启动方式 | python app.py → http://localhost:8000 |

### 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11、FastAPI、uvicorn、httpx |
| OCR | PaddleOCR 外部 API（Case 1/2） |
| AI Agent | GPTBots 新加坡节点，blocking 模式 |
| PDF / 图片 | PyMuPDF：读取、逐页渲染、译图校验和 PDF 合并 |
| 报告 | python-docx + docx2pdf（Case 1/2/5） |
| 爬虫 | Playwright sync API，经线程池桥接 asyncio（Case 5） |
| 前端 | 原生 HTML/CSS/JS、fetch、SSE、marked.js，无构建流程 |
| 数据 | 三个进程内字典，无数据库、鉴权或多实例共享 |

### Use Case 总览

| Case | 状态 | 输入与处理 | 输出 / result_key |
|---|---|---|---|
| Case 1 | 已实现、功能锁定 | 双方 PDF → OCR → Agent A → Agent B | 双方问题清单；甲方 / 乙方 / 通用 |
| Case 2 | 已实现、功能锁定 | 多份调解材料 → OCR → Agent A → Agent C | 调解员简报；调解员简报 |
| Case 4 | 已发布 | 单份 PDF → PyMuPDF 拆页 → Agent I 译图 → PDF 合并 | 译文 PDF；译文PDF |
| Case 5 | 已发布 | 关键词 → HKLII 抓取 → Agent J | 案例摘要；HKLII 案例摘要 |
| Case 3 / 6B | 待开发 | input_example/ 中已有部分样本 | — |

Case 4 与 Case 5 随 case4-case5-v1.0 一起交付。Case 4 不经过 PaddleOCR；页面识别、
翻译与重新排版由 Agent I 的多模态图片模型完成。

---

## 2. 目录结构

~~~text
eBRAM3---OCR--/
├── app.py                         # FastAPI 入口、生命周期、页面路由
├── requirements.txt               # Python 依赖
├── .env.example                   # 配置模板；真实 .env 被忽略
├── api/
│   ├── pdf_chat.py                # 公共 PDF/SSE/分析/下载/追问与 Session store
│   ├── case4_routes.py            # Case 4 单 PDF 上传入口
│   ├── case5_routes.py            # Case 5 HKLII 搜索与抓取 SSE
│   ├── chat.py                    # 旧 conversation/chat 接口与健康检查
│   └── graph_route.py             # 旧图表 chat 接口
├── cases/
│   ├── base.py                    # CaseHandler + SseEvent 契约
│   ├── __init__.py                # REGISTRY / get_handler()
│   ├── case1_party_questions.py
│   ├── case2_mediator_briefing.py
│   ├── case4_pdf_translation.py
│   └── case5_hklii_search.py
├── model/
│   ├── config.py                  # Agent/OCR 配置、URL 与请求头
│   ├── report_generator.py        # DOCX/PDF 报告
│   ├── pdf_processor.py           # 公共 PDF 工具
│   ├── schemas.py
│   └── utils.py
├── scraper/
│   └── hklii.py                   # HKLII 搜索、正文抓取及异常类型
├── static/
│   ├── index.html                 # Case 选择页
│   ├── common.js / style.css      # 公共工具与设计令牌
│   ├── case1.html / case1.js
│   ├── case2.html / case2.js
│   ├── case4.html / case4.js / case4.css
│   ├── case5.html / case5.js
│   └── fonts/                     # 自托管 Outfit 字体
├── GPTbots_.bot/
│   ├── Case4.bot                  # 用户导出的 Agent I 原始配置
│   └── generated/
│       ├── build_case4_agent.py   # 可重复生成脚本
│       ├── Case4-Agent-I.bot      # 已校验生成物
│       ├── overview.md            # Agent I 设计与 POC 记录
│       └── prompts/AI Model-1.md  # Agent I 运行 Prompt
├── tests/
│   ├── test_case4_agent_builder.py
│   └── test_case4_backend.py
├── docs/HANDOVER.md               # 本文件，项目权威交接说明
└── input_example/                 # 客户/测试样本，不是运行时依赖
~~~

---

## 3. 核心架构契约

### 3.1 CaseHandler 与 Registry

所有业务 Case 实现 cases/base.py 的四个方法：

| 方法 | 责任 | 返回 |
|---|---|---|
| analyze() | 调用外部能力、写结果并逐步产出事件 | AsyncGenerator[SseEvent, None] |
| generate_report() | 生成可下载文件 | (file_bytes, filename, media_type) |
| followup_chat() | 上下文追问；不支持时明确拒绝 | conversation_id + reply |
| get_downloadable_keys() | 返回已成功写入结果仓库的键 | list[str] |

当前 Registry：

~~~python
REGISTRY = {
    "case1": Case1Handler,
    "case2": Case2MediatorBriefingHandler,
    "case4": Case4PdfTranslationHandler,
    "case5": Case5HkliiSearchHandler,
}
~~~

结果必须先写入 session_results_store，再发送对应 result 事件，保证前端收到事件后
可以立即下载。

### 3.2 SSE 事件边界

POST /pdf/session/analyze 的路由生命周期事件：

- analyze_start
- Handler 透传的 progress / result / 局部 error
- 正常结束时 analyze_complete
- 未捕获异常时 fatal_error，随后关闭流

Handler 的 SseEvent.data 由各 Case 自治；result.result_key 必须与
get_downloadable_keys() 一致。

Case 5 的 /case5/scrape 另有 progress、scrape_complete、no_results、
search_timeout 和 error 事件。

### 3.3 Session 数据流

| Store | 当前内容 |
|---|---|
| session_store | Case 1/2 文档结果、Case 4 Case4PdfDocument、Case 5 ScrapedResult |
| session_metadata | case_type 及文件名、页数、方向或搜索关键词 |
| session_results_store | 最终结果、conversation id、创建时间与报告数据 |

_get_handler() 优先读取 session_metadata.case_type，其次读取最终结果中的
case_type，最后仍保留 case1 兼容 fallback。

后台每 30 分钟扫描一次，带 created_at 且超过两小时的 Case 1/2/4 会话会被清理。
ScrapedResult 没有 created_at，Case 5 目前会跳过 TTL，是已登记技术债务。

### 3.4 主要路由

| 方法 | 端点 | 说明 |
|---|---|---|
| GET | /、/case1、/case2、/case4、/case5 | 页面入口 |
| POST | /pdf/pages/chat | Case 1/2：上传、OCR、Agent A 逐页处理 |
| POST | /case4/session/upload | Case 4：校验并暂存单份 PDF 与方向 |
| POST | /case5/scrape | Case 5：搜索 HKLII 并抓取正文（SSE） |
| POST | /pdf/session/analyze | 按 case_type 调用 Handler（SSE） |
| POST | /pdf/session/report | 下载报告；参数名 party 实际承载 result_key |
| POST | /pdf/session/chat | 追问；Case 4 明确拒绝 |
| POST | /pdf/chat | 旧整份 PDF 处理端点 |
| GET | /health | 健康检查 |
| POST | /conversation、/chat、/graph/chat | 旧兼容接口，非新 Case 主路径 |

### 3.5 GPTBots 调用约定

Case 1/2/5 的文档附件必须使用数组结构：

~~~python
{"type": "document", "document": [doc_item]}
~~~

Case 4 每次发送一页图片：

~~~python
{
    "response_mode": "blocking",
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "方向和页码指令"},
            {"type": "image", "image": [{
                "base64_content": "...", "format": "png", "name": "page_0001.png"
            }]},
        ],
    }],
    "conversation_config": {"short_term_memory": False, "long_term_memory": False},
}
~~~

| Agent | 配置变量 | 职责 | Case |
|---|---|---|---|
| Agent A | api_key | PDF 逐页摘要与结构化字段 | 1 / 2 |
| Agent B | AGENT_B_API_KEY | 双方争议综合分析 | 1 |
| Agent C | AGENT_C_API_KEY | 调解员简报 | 2 |
| Agent I | AGENT_I_API_KEY | 英文与繁体中文页面图片双向翻译 | 4 |
| Agent J | AGENT_J_API_KEY | HKLII 案例摘要 | 5 |

---

## 4. 各 Case 实现要点

### Case 1 — 双方谈判问题生成

- 甲乙方 PDF 分别上传，party 决定归属；PaddleOCR 后由 Agent A 逐页处理。
- Handler 按方合并材料并调用 Agent B；可部分成功。
- result_key 为甲方、乙方或通用。
- 支持 DOCX/PDF 下载和上下文追问。

### Case 2 — 调解员简报

- 多份材料可标记为甲方、乙方或通用；OCR/Agent A 后按 party 合并。
- Agent C 生成一份 Markdown 简报，result_key 为调解员简报。
- 支持 DOCX/PDF 下载和上下文追问。

### Case 4 — 双语 PDF 页面翻译

上传接口：

~~~text
POST /case4/session/upload
pdf_file: PDF
direction: en_to_zh_tw | zh_tw_to_en
~~~

接口返回 session_id、filename、page_count 和 direction。校验规则：单文件、
PDF 魔数、非空、可读取、未加密、最大 25 MB、最多 30 页。

处理链：

1. PyMuPDF 将页面渲染为 RGB、无透明通道的 PNG，默认 150 DPI。
2. 单页超过 9.5 MB 时依次降至 120、96 DPI；仍超限则整份失败。
3. 整份 PDF 只创建一个 Agent I conversation，页面按顺序串行发送。
4. 优先解析 blocking 响应里的 URL/base64；没有图片时查询 /v2/messages，只选择
   Assistant 的 branch_content[].image[]，绝不选择用户上传的原图。
5. GPTBots 临时 URL 必须是官方 HTTPS 域名；译图最大 20 MB，并验证 PNG/JPEG/WebP
   文件头、尺寸和可解码性。
6. 会话详情若返回 /thumbnail/ URL，先尝试去除该路径取得原始译图；只有原图下载
   失败时才回退缩略图。当前样例原图为 864×1222，缩略图为 566×800。
7. 429、500、502、503、504、超时和传输错误最多尝试三次；每次页面调用超时 300 秒。
8. 所有译图完成后，按原始页数、页序和页面尺寸居中铺放到白色页面并生成 PDF。

任一页最终失败则不生成部分 PDF。Case 4 的唯一 result_key 是译文PDF，只支持
PDF 下载，不显示聊天输入，也拒绝 /pdf/session/chat 追问。

Agent I 源文件与 POC 记录位于 GPTbots_.bot/。当前测试模式版本为 v1.0.3，记忆、
用户属性、工具和推理展示均关闭，输出类型固定为 Image。生成式图片翻译仍可能对复杂
表格、密集条款、印章、手写内容或产品名产生偏差，必须由人工复核。

### Case 5 — HKLII 案例检索

- /case5/scrape 先搜索 HKLII，再以最多三个正文抓取并发任务处理前 10 条结果。
- Playwright sync API 通过全局线程池运行，避免阻塞 asyncio 事件循环。
- 抓取结果写入 Session 后，由 Agent J 生成 HKLII 案例摘要。
- 支持 DOCX/PDF 下载和基于 Agent J conversation 的追问。
- 无结果和搜索超时分别返回 no_results、search_timeout，不进入摘要阶段。

---

## 5. 前端与设计基线

- 原生 HTML/CSS/JS，无构建流程；fetch() 消费 SSE，marked.js 渲染 Markdown。
- 保持 248px 左侧导航、Outfit 字体、深浅主题、卡片边框和克制的法律科技视觉。
- Case 4 使用低饱和铜金色和“拆页 → 翻译 → 合并”三阶段进度，不显示聊天输入框。
- Case 1/2/5 保留上传或搜索、分析结果、报告下载和追问流程。
- 新 Case 应新增独立页面和 Handler，但复用公共设计令牌、Session 与 SSE 契约。
- 动效只表达状态，并兼容 prefers-reduced-motion；移动端不能依赖固定侧栏宽度。

---

## 6. 测试与调试

当前自动化测试集中覆盖 Case 4，共 18 项：

~~~powershell
python -m unittest discover -s tests -v
node --check static\case4.js
python C:\Users\<user>\.agents\skills\gptbots-agent-skill\scripts\validate_gptbots_config.py GPTbots_.bot\generated\Case4-Agent-I.bot --json
~~~

覆盖内容包括 Agent .bot 生成、上传校验、25 MB/30 页边界、加密与损坏 PDF、payload、
blocking/base64/会话详情图片解析、官方 URL 校验、原图回退、重试、DPI 降级、PDF 页序
与尺寸、PDF-only 下载及拒绝追问。

调试原则：

- 先复现并写失败测试，再修复；外部 Agent 的真实响应应脱敏后固化为契约样例。
- 排查 DOM 时序可临时使用 headless=False，但提交代码保持生产默认。
- Playwright 等待优先使用互斥 selector，避免无结果页面一直等待单一 selector。
- docx2pdf 在 CI 或无 Word 环境可能失败，测试应验证 DOCX 降级路径。

---

## 7. 已知限制与下一步

- **凭证风险**：旧公开历史中的 Agent A、Agent B 与 PaddleOCR 凭证尚未轮换。
- **持久化与多实例**：Session 全在进程内，服务重启即丢失，也不能跨 worker 共享。
- **Case 5 TTL**：ScrapedResult 没有 created_at，当前清理任务会跳过 Case 5。
- **鉴权**：当前没有登录、权限控制或租户隔离，不应直接暴露到公网。
- **测试覆盖**：Case 1/2/5 缺少系统化自动回归和视觉基线。
- **前端安全**：AI Markdown 通过 marked.js 写入页面，尚未增加显式 HTML 清洗。
- **依赖与环境**：DOCX 转 PDF 依赖本机 Word；HKLII 依赖站点结构和网络可达性。
- **后续 Case**：Case 3/6B 尚未实现；新增时遵守 Handler、Registry、Session 和 SSE 契约。
- **仓库体积**：input_example/ 包含大量二进制样本，可后续迁移至 Git LFS 或独立样本仓库。

文档维护规则：每次功能 PR 合并时，同时核对 README、本文档、.env.example、依赖注释、
路由表和 Case 状态；代码实现永远是最终事实来源。
