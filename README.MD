# README
---

```markdown
# eBRAM AI 文档助手

> 专为 eBRAM 在线仲裁平台设计的 AI 文档分析工具。上传仲裁案件 PDF 材料，系统自动 OCR 识别、逐页 AI 分析、多文档综合对比，结果实时流式输出。

## 功能概览

- **PDF OCR**：上传扫描件 PDF，调用飞桨 PaddleOCR 官方 API 识别文字，支持中英双语混合
- **逐页 AI 分析**：由 GPTBots Agent A 逐页提取摘要和结构化字段（当事方、金额、时间线等）
- **综合分析**：Agent B 汇总所有页面信息，输出完整的案件分析报告
- **多文档模式**：同时上传多份材料，全部处理完成后统一综合分析
- **文字追问**：分析完成后可继续与 AI 对话，提问基于文档内容
- **实时进度**：SSE 流式推送，OCR 进度 + 页级分析进度实时可见
- **深色/浅色主题**，对话历史记录（localStorage）

## 系统要求

- Python 3.11（推荐使用 Conda 环境）
- GPTBots 账号（需配置 Agent A 和 Agent B 两个 Agent）
- 飞桨 AI Studio 账号（获取 PaddleOCR API Token）

## 快速开始

### 1. 配置环境

```bash
conda create -n ebram python=3.11
conda activate ebram
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple
```

### 2. 配置密钥

```bash
cp .env.example .env
# 编辑 .env，填入以下三项：
# api_key=app-xxxxxxx          （Agent A 的 GPTBots API Key）
# AGENT_B_API_KEY=app-xxxxxxx  （Agent B 的 GPTBots API Key）
# PADDLE_OCR_TOKEN=xxxxxxx     （飞桨 AI Studio 的访问令牌）
```

### 3. 启动服务

**命令行：**
```bash
python app.py
```

浏览器访问：[http://localhost:8000](http://localhost:8000)

## 接口文档

启动后访问 [http://localhost:8000/docs](http://localhost:8000/docs) 查看自动生成的 OpenAPI 文档。

| 端点 | 方法 | 说明 |
|---|---|---|
| `/chat` | POST | 多轮文字聊天（Agent B）|
| `/pdf/pages/chat` | POST | PDF 逐页分析（SSE 流式）|
| `/pdf/session/analyze` | POST | 多文档综合分析（SSE 流式）|
| `/pdf/chat` | POST | 简易 PDF 分析（非流式，仅测试用）|
| `/graph/chat` | POST | 单轮对话（Agent A，遗留接口）|
| `/health` | GET | 健康检查 |

## 技术架构

```
前端 (Vanilla JS + SSE)
    ↕ HTTP / SSE
后端 (FastAPI + uvicorn)
    ├── PaddleOCR API   ← PDF OCR（云端，轮询）
    ├── GPTBots Agent A ← 逐页分析（结构化输出 PART_A/B）
    └── GPTBots Agent B ← 综合分析 + 文字追问
```

## 项目结构

```
api/           HTTP 路由（chat.py / pdf_chat.py / graph_route.py）
model/         配置、数据模型、工具函数
static/        前端静态文件（index.html / style.css / app.js）
db/            待实现：数据库持久化层
agent/         待实现：本地 Agent 封装
tool/          待实现：扩展工具函数
```

## 已知限制

- 会话数据存储在内存中，**服务重启后清空**
- PaddleOCR 最大等待时间为 10 分钟（120次×5秒）
- 多文档处理为串行模式（非并发），大量文件时耗时较长
```
