# eBRAM AI 文档助手 —— 项目上下文文档

> **文档用途**：新 LLM 窗口开场上下文。粘贴本文档后，AI 无需读代码即可准确理解项目现状、锁定决策和待办事项。
> **最后更新**：2026-05-14

---

## 第 1 节：项目基本信息

| 项 | 值 |
|---|---|
| 项目名称 | eBRAM AI 文档助手（内部代号 eBRAM3） |
| 本地路径 | `C:\Users\86137\Desktop\eBRAM3 - OCR成功` |
| 当前分支 | `dev` |
| Python 环境 | conda `ebram`，Python 3.11（**不用系统 Python 3.8**） |
| 启动命令 | `python app.py` → `http://localhost:8000` |
| 远程仓库 | `origin/dev`（本地领先若干 commits，手动 push） |

### 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | FastAPI + uvicorn |
| OCR | 飞桨 PaddleOCR 官方 API（aistudio.baidu.com，轮询式） |
| AI Agent | GPTBots（新加坡节点，`api-sg.gptbots.ai`） |
| 前端 | Vanilla JS + Server-Sent Events（SSE） |
| 报告生成 | python-docx + WeasyPrint（PDF） |
| 依赖管理 | pip + requirements.txt |

---

## 第 2 节：系统架构

### 2.1 请求流程

```
用户浏览器
    ↕ HTTP / SSE
FastAPI 后端 (app.py)
    ├── api/pdf_chat.py     — PDF 上传、OCR、分析路由（SSE 流式）
    ├── api/chat.py         — 文字追问路由
    └── cases/              — Case Handler 层（业务逻辑）
            ├── base.py                          CaseHandler ABC + SseEvent
            ├── case1_party_questions.py         Case 1 实现
            ├── case2_mediator_briefing.py       Case 2 实现
            └── __init__.py                      REGISTRY + get_handler() 工厂
```

### 2.2 外部服务

| 服务 | 用途 | 配置键 |
|---|---|---|
| PaddleOCR API | PDF → 每页 Markdown 文本 | `PADDLE_OCR_TOKEN` |
| GPTBots Agent A | 逐页结构化摘要（PART_A + PART_B） | `api_key` |
| GPTBots Agent B | Case 1 综合分析 | `AGENT_B_API_KEY` |
| GPTBots Agent C | Case 2 调解员简报 | `AGENT_C_API_KEY` |

### 2.3 核心数据结构

**DocResult**（`api/pdf_chat.py`）

```python
@dataclass
class DocResult:
    filename: str
    summaries: list[str]   # PART_A：每页文本摘要（index 对应页码）
    merged_fields: dict    # PART_B：跨页合并后的结构化字段（JSON）
    party: str             # "甲方" / "乙方" / "通用"
```

**merged_fields 主要字段**：`submitting_party_info`、`case_overview`、`submitting_party_objectives`、`timeline_events`、`disputed_issues`、`amounts`、`emotional_cues`、`gaps_and_issues`、`other_party_expected_positions`

**Session Store 结构**

```python
session_store[session_id]         = list[DocResult]    # OCR + Agent A 原始结果

session_results_store[session_id] = {
    "case_type": "case1" | "case2",
    "created_at": float,
    "results": {
        "<result_key>": {
            "analysis_text": str,       # Agent B/C 输出的完整 Markdown
            "conversation_id": str,     # 用于后续追问
        }
    }
}

session_metadata[session_id] = {"case_type": str}      # 路由分发用
```

**TTL**：2 小时，30 分钟周期清理（`app.py` 后台任务）。

### 2.4 GPTBots API Payload 格式（已锁定，见 D1）

```python
# 创建 conversation
POST /v1/conversation
{"user_id": "some_user"}

# 发消息（纯文本）
POST /v2/conversation/message
{
    "conversation_id": str,
    "response_mode": "blocking",
    "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
}

# 发消息（带文档附件）
POST /v2/conversation/message
{
    "conversation_id": str,
    "response_mode": "blocking",
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "document", "document": [
                {"base64_content": b64_str, "format": "md", "name": "file.md"}
            ]}
        ]
    }]
}
```

---

## 第 3 节：Case 模块状态

### Case 1 — 当事方问题分析 🟢 已锁定 (v1.0)

| 项 | 详情 |
|---|---|
| 状态 | 🟢 生产稳定 |
| 入口 | `/case1` → `static/case1.html` |
| 上传 | 甲方 PDF × N + 乙方 PDF × M（数量不限，各方独立分析） |
| 分析流程 | Agent A（逐页）→ Agent B（甲方综合 + 乙方综合） |
| 输出 | 甲方报告 + 乙方报告（各 1 份 Word/PDF） |
| result_keys | `"甲方"` / `"乙方"` |
| 追问 | 新建 conversation + 注入分析上下文 |
| 已知问题 | 无 |

**关键设计要点**：
- Agent B 接收格式：每份 PDF → 1 个独立 md document item，内含 PART_A 摘要 + `---` + PART_B JSON fenced block
- 失败方不写占位符进 store，避免前端误判为"已完成"
- `get_downloadable_keys()` 仅返回已成功写入 store 的 key

---

### Case 2 — 调解员简报生成 🟢 已锁定 (v1.0，2026-05-14)

| 项 | 详情 |
|---|---|
| 状态 | 🟢 生产稳定 |
| 入口 | `/case2` → `static/case2.html` |
| 上传 | 任意数量 PDF，前端 UI 由用户指定 party（甲方 / 乙方 / 通用） |
| 分析流程 | Agent A（逐页）→ 按 party 分组合并 → Agent C（简报生成） |
| 输出 | 调解员简报（1 份 Word/PDF） |
| result_key | `"调解员简报"` |
| 追问 | 新建 conversation + 注入简报上下文 |
| 已知问题 | 无 |

**关键设计要点**：
- 按 party 分组合并：N 份 PDF → 最多 3 个 md document item（CLAIMANT / RESPONDENT / COMMON/NEUTRAL）；空分组不生成文件
- 每个合并 md 内部用 `===== 文档分隔 =====` 隔开多份原始材料
- 合并 md 首行为英文 party 标签（`[CLAIMANT]` 等），Agent C 据此识别来源方
- 固定文件名：`claimant_materials.md` / `respondent_materials.md` / `common_materials.md`
- Agent C 输出目标：7 模块简报（A 案件背景 / B 纠纷性质 / C 双方立场 / D 双方利益 / E 待厘清议题 / F 共同目标 / G BATNA/WATNA）

---

## 第 4 节：Agent 档案

### Agent A — 逐页分析 Agent

| 项 | 值 |
|---|---|
| 配置键 | `api_key` |
| 调用时机 | 每页 OCR Markdown 文本 → PART_A + PART_B |
| 输入 | 单页 OCR 纯文本 |
| 输出格式 | `===PART_A_START===` ... `===PART_A_END===` + `===PART_B_START===` ... `===PART_B_END===` |
| PART_A | 当页文字摘要 |
| PART_B | 当页结构化 JSON（跨页 merge 后形成 `merged_fields`） |
| 复用范围 | Case 1 + Case 2 均使用，逻辑封装在 `api/pdf_chat.py` |
| 版本状态 | ⚠️ 版本号/Prompt 版本待确认 |

### Agent B — Case 1 综合分析 Agent

| 项 | 值 |
|---|---|
| 配置键 | `AGENT_B_API_KEY` |
| 调用时机 | Case 1 全部 PDF 处理完毕后 |
| 输入 | 每份 PDF 对应 1 个 md document item（PART_A + PART_B） |
| 输出 | 甲乙双方完整分析报告（Markdown） |
| Conversation 策略 | 甲方 / 乙方各建独立 conversation |
| 版本状态 | ⚠️ 版本号/Prompt 版本待确认 |

### Agent C — Case 2 调解员简报 Agent

| 项 | 值 |
|---|---|
| 配置键 | `AGENT_C_API_KEY` |
| 调用时机 | Case 2 全部 PDF 处理完毕后 |
| 输入 | 按 party 分组的合并 md（最多 3 份）+ 固定英文 text prompt |
| 输出 | 结构化调解员简报（Markdown，目标 7 模块 A-G） |
| Conversation 策略 | 单 conversation 接收全部材料 |
| 固定 text prompt | `"Please prepare the mediator briefing based on the attached materials..."` |
| 版本状态 | ✅ v1.0 端到端验证通过（2026-05-14） |

---

## 第 5 节：已锁定决策

> 以下决策经过实现与验证后锁定，**非用户明确指令不得推翻**。

**[D1]** GPTBots API payload 格式：必须使用 `messages[{role, content: [{type, text/document}]}]` 格式；顶层 `query` / `documents` 字段会被静默忽略或返回 400，**绝对不能使用**。

**[D2]** Case Handler 架构：`CaseHandler` 抽象基类（`cases/base.py`）定义 4 个方法契约；每个 Case 新建独立文件继承实现；路由层 `api/pdf_chat.py` **完全不动**，通过 `get_handler(case_type)` 工厂分发。

**[D3]** `session_store` 与 `session_results_store` 分离：前者存 OCR + Agent A 原始结果（DocResult 列表），后者存分析报告（Agent B/C 输出）；两者独立 TTL，互不依赖。

**[D4]** Handler 错误规范：只抛 `ValueError`（业务逻辑错误）或 `RuntimeError`（外部服务失败），**不抛 `HTTPException`**；路由层负责将这两种异常转换为 HTTP 4xx/5xx。

**[D5]** Agent A 输出解析：PART_A / PART_B 以固定分隔符提取；PART_B JSON 解析失败时降级为空 dict，**不中断** OCR 流程。

**[D6]** 所有 GPTBots 调用使用 `response_mode: "blocking"`；FastAPI SSE 层负责向前端推送进度，与 GPTBots 的 streaming 模式无关。

**[D7]** `followup_chat()` 策略：每次追问**新建独立 conversation**，注入分析/简报文本作为上下文，再发送用户问题；不复用 analyze conversation（避免长对话 token 积累）。

**[D8]** analyze 结果写入 store 的条件：仅在 Agent B/C **成功返回**后写入；失败方不写占位符（防止前端误判为"已完成"）。

**[D9]** Case 2 result_key 为单一字符串 `"调解员简报"`，不按甲乙方拆分。`get_downloadable_keys()` 返回 `["调解员简报"]` 或 `[]`。

**[D10]** Agent A → Agent B/C 的文档格式（Case 1 & Case 2 共用）：每份 PDF 构造为独立 md document item；内容格式：PART_A 摘要 + `---` 分隔符 + PART_B JSON fenced block；md 文件名为 `{原文件名（去扩展名）}.md`。

**[D11]** Case 2 Agent C 输入采用按 party 分组合并策略：N 份 PDF → 最多 3 个 md document item（CLAIMANT / RESPONDENT / COMMON/NEUTRAL） — 单份独立 item 会让 Agent C 上下文碎片化，影响综合判断；分组合并后 Agent C 输出质量显著提升（v1.0 验证）。

---

## 第 6 节：已知技术债务

| 优先级 | 问题 | 影响 | 备注 |
|---|---|---|---|
| P2 | 会话数据全内存存储，重启后清空 | 测试期可接受 | `db/` 目录已预留 |
| P2 | 无用户鉴权 | 仅限内网环境使用 | 基本 Auth 待评估 |
| P3 | PaddleOCR 串行处理（无并发） | 多文件时耗时较长 | 并发改造待评估 |
| P3 | 报告样式硬编码在 report_generator.py | 格式调整成本高 | 模板化待评估 |

---

## 第 7 节：开发状态

### 已完成功能

| 功能 | 完成时间 |
|---|---|
| Case 1 完整流程（上传 → OCR → 分析 → 下载 → 追问） | 2026-05 重构完成 |
| CaseHandler 抽象基类 + REGISTRY 工厂 | 2026-05 |
| Case 2 前端 UI（复用 Case 1 UX 风格） | 2026-05-14 |
| Case 2 完整流程端到端验证（上传 → 分析 → 下载 → 追问） | 2026-05-14 |

### 进行中事项

| 事项 | 说明 |
|---|---|
| Case 3+ 需求收集 | 尚未启动，等待用户定义 |

### 待办事项

| 优先级 | 事项 |
|---|---|
| P2 | Session 持久化（数据库，`db/` 目录已预留） |
| P2 | 用户鉴权 |
| P3 | PaddleOCR 并发处理 |
| P3 | 报告模板化 |

### 变更日志

| 日期 | 版本 | 主要变更 | 备注 |
|---|---|---|---|
| 2026-05-14 | Case 2 v1.0 | 按 party 分组合并 md 策略落地；移除 debug 日志与死代码（_classify_filename 等）；docstring 通用化 | Case 2 状态升级至 🟢 生产稳定 |
| 2026-05-14 | Case 2 v0.2 | Agent C payload 格式修复（messages[] 格式）；PART_B Structured Data 补入 md document item | 完整流程跑通 |
| 2026-05-14 | Case 2 v0.1 | Case 2 Handler 骨架 + 前端页面（复用 Case 1 UX） | 首次接入 |
| 2026-05 | Case 1 v1.0 | CaseHandler 重构（S1-S9）；前端拆分 case1.js/case2.js | 烟雾测试通过，tag: `refactor-case1-handler-v1.0` |

---

## 第 8 节：新窗口接管指南

### 8.1 接管前必读（5 分钟）

1. 通读本文档第 1–7 节
2. 确认当前分支是 `dev`
3. 确认 `.env` 中 4 个密钥已配置：`api_key`、`AGENT_B_API_KEY`、`AGENT_C_API_KEY`、`PADDLE_OCR_TOKEN`
4. 启动服务：`python app.py`，访问 `http://localhost:8000`

### 8.2 新窗口第一句话模板

> "我已阅读项目上下文文档（`docs/project-context.md`）。Case 1/2 均已锁定为 v1.0 生产稳定状态。请告诉我本次需要做什么。"

### 8.3 新窗口自动遵守的禁止事项

- ❌ 不改 `api/pdf_chat.py` 路由层（除非用户明确指示）
- ❌ 不改 `cases/base.py` 的 CaseHandler 契约（除非用户明确指示）
- ❌ 不改 GPTBots payload 格式（见 D1）
- ❌ 不 push 到远程（等用户手动 push）
- ❌ 不修改 `.env` 文件内容

### 8.4 新增 Case 的标准流程

1. 新建 `cases/caseN_xxx.py`，继承 `CaseHandler`，实现 4 个方法
2. 在 `cases/__init__.py` 的 `REGISTRY` 注册 `"caseN": CaseNHandler`
3. 新建 `static/caseN.html` + `static/caseN.js`
4. 在 `app.py` 注册 `/caseN` 静态路由
5. 在 `.env.example` 新增对应 Agent API Key

---

## 第 9 节：附录

### 文件结构速查

```
eBRAM3 - OCR成功/
├── app.py                              FastAPI 入口 + 路由注册
├── .env                                密钥配置（不入库）
├── .env.example                        密钥配置模板
├── requirements.txt
├── api/
│   ├── pdf_chat.py                     PDF 上传/OCR/分析路由（薄包装层）
│   └── chat.py                         文字追问路由
├── cases/
│   ├── base.py                         CaseHandler ABC + SseEvent dataclass
│   ├── __init__.py                     REGISTRY + get_handler() 工厂函数
│   ├── case1_party_questions.py        Case 1 Handler 实现
│   └── case2_mediator_briefing.py      Case 2 Handler 实现
├── model/
│   ├── config.py                       环境变量读取 + API 配置 + 请求头构造
│   ├── utils.py                        extract_gptbots_reply 等工具函数
│   └── report_generator.py             Markdown → Word/PDF 报告生成
├── static/
│   ├── case1.html / case1.js           Case 1 前端
│   ├── case2.html / case2.js           Case 2 前端
│   ├── common.js                       公共前端逻辑（SSE、上传、聊天框）
│   └── style.css                       全局样式
└── docs/
    ├── refactor/case1-handler-v1.0.md  Case 1 重构归档文档
    ├── handover/case2-design-handover.md  Case 2 设计期交接文档（历史存档）
    └── project-context.md              ← 本文档（新窗口接管用）
```

### Agent 版本快照

| Agent | 配置键 | 描述 | 状态 |
|---|---|---|---|
| Agent A | `api_key` | 逐页分析，输出 PART_A/B | ⚠️ Prompt 版本号待确认 |
| Agent B | `AGENT_B_API_KEY` | Case 1 综合分析，甲乙双方 | ⚠️ Prompt 版本号待确认 |
| Agent C | `AGENT_C_API_KEY` | Case 2 调解员简报，7 模块 A-G | ✅ v1.0 验证通过 |

### 协作分工边界（2026-05-14 确认）

**用户职责**：
- GPTBots 平台上 Agent A/B/C 的提示词设计、调优、版本管理
- Agent 输出结构定义（如 PART_A/PART_B 格式约定）
- Agent 后台日志分析与效果验证

**代码侧（Claude / Claude Code）职责**：
- FastAPI 后端实现
- 前端 HTML/JS 实现
- Agent 调用 payload 封装（输入格式）
- Agent 返回结果解析与下游处理（输出格式）
- Case Handler 架构、Session 管理、报告生成
- 端到端流程串联

**接口契约（双方约定）**：
- 用户定义：Agent 需要的输入格式、Agent 的输出格式
- 代码实现：把输入塞进去、把输出接住、整合到流程

---

*最后更新：2026-05-14*
