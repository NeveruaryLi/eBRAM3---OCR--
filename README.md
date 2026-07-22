# eBRAM AI 文档助手（eBRAM3）

> 为 eBRAM 调解/仲裁平台提供 AI 辅助文档分析。上传案件 PDF，自动 OCR、逐页 AI 分析、
> 多文档综合，结果通过 SSE 实时流式输出；支持双语 PDF 翻译、HKLII 案例检索与
> Word/PDF 报告下载。

> 📖 **接手开发前请先读 [`docs/HANDOVER.md`](docs/HANDOVER.md)** —— 单一权威交接文档，
> 含架构契约、各 Case 实现要点、开发陷阱与「拉下来就能跑」步骤。

---

## 功能概览（Use Cases）

| Case | 说明 |
|---|---|
| **Case 1** | 上传双方 PDF → 逐页摘要 → 综合分析 → 生成双方谈判问题清单 |
| **Case 2** | 多份调解材料 → 生成调解员简报（Markdown） |
| **Case 4** | 英文 ↔ 繁体中文 PDF 逐页翻译 → 保持原页尺寸并生成译文 PDF |
| **Case 5** | 关键词 → Playwright 爬取 HKLII 前 10 条案例 → AI 摘要 |

通用能力：飞桨 PaddleOCR 识别、GPTBots 多 Agent 分析、SSE 实时进度、文字追问、
Word/PDF 报告下载、深色/浅色双主题。

## 快速开始

```bash
# 1. 环境（务必 Python 3.11）
conda create -n ebram python=3.11 -y && conda activate ebram

# 2. 依赖
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple
playwright install chromium          # Case 5 爬虫内核（仅首次）

# 3. 密钥：复制模板并填入真实 key
cp .env.example .env                 # 编辑 .env，替换占位符

# 4. 启动 → http://localhost:8000
python app.py
```

必填密钥（缺失会阻止启动）：`api_key`、`AGENT_B_API_KEY`、`PADDLE_OCR_TOKEN`。
Case 2/4/5 另需 `AGENT_C_API_KEY` / `AGENT_I_API_KEY` / `AGENT_J_API_KEY`；
未配置 `AGENT_I_API_KEY` 时仅 Case 4 不可用。详见 `.env.example` 与
[`docs/HANDOVER.md`](docs/HANDOVER.md) §0。

> **Windows**：PDF 报告经 `docx2pdf` 转换，依赖本机安装的 Microsoft Word。

## 主要端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/` `/case1` `/case2` `/case4` `/case5` | GET | 页面 |
| `/pdf/pages/chat` | POST | 上传 PDF → OCR → 逐页分析（SSE） |
| `/pdf/session/analyze` | POST | 综合分析（SSE，按 case_type 分派） |
| `/pdf/session/report` | POST | 下载 Word/PDF 报告 |
| `/pdf/session/chat` | POST | 文字追问 |
| `/case4/session/upload` | POST | 上传单份 PDF 与翻译方向（Case 4 专属） |
| `/case5/scrape` | POST | HKLII 爬取（SSE，Case 5 专属） |

启动后 `http://localhost:8000/docs` 查看自动生成的 OpenAPI 文档。

### Case 4 处理流程

Case 4 将上传的 PDF 以 150 DPI 逐页渲染为 PNG（单页过大时自动降至 120/96 DPI），
再按页串行发送给 Agent I，最后将译图按原始页序和页面尺寸合并为 PDF。任一页面最终
失败时整份任务失败，不会提供不完整译文。GPTBots 会话详情地址可通过 `messages_url`
覆盖，默认值已写入 `.env.example`。

## 技术栈

FastAPI + uvicorn ｜ 原生 JS + SSE ｜ PaddleOCR 官方 API ｜ GPTBots（新加坡节点）｜
PyMuPDF ｜ python-docx + docx2pdf ｜ Playwright。

## 项目结构

```
app.py        FastAPI 入口（路由挂载 + 配置校验）
api/          HTTP 路由层（pdf_chat / case4_routes / case5_routes / chat / graph_route）
cases/        Handler 层（base 契约 + Case 1/2/4/5 实现 + REGISTRY）
model/        基础设施（config / report_generator / pdf_processor / utils / schemas）
scraper/      Case 5 HKLII 爬虫（Playwright）
static/       前端（每个 Case 一套 html+js，视觉独立）
docs/         HANDOVER.md（权威交接文档）
input_example/ 各 Usecase 测试样本
```

## 已知限制

- 会话数据存内存，**服务重启即清空**。
- PaddleOCR 最长等待 10 分钟（120×5s）。
- 多文档为串行处理，文件多时较慢。
- Case 4 每次只接收一份 PDF，最大 25 MB、30 页；仅提供译文 PDF 下载，不支持追问。
- Case 4 下载 GPTBots 原始译图并按原页尺寸合并；若原图暂不可用，才回退至缩略图。
- 图片翻译由生成式模型完成，复杂表格、印章、手写内容与密集条款仍应人工复核。

---

更多架构细节、决策记录与开发陷阱见 [`docs/HANDOVER.md`](docs/HANDOVER.md)。
