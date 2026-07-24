# Smart Gateway

## 📖 项目概述

Smart Gateway 是一个智能的 LLM（大语言模型）聚合网关，提供 OpenAI 兼容的 API 接口，能够统一访问多个 LLM 提供商。它具备智能模型路由、自动供应商发现、基于评分的模型选择、上下文压缩以及完整的管理后台等特性，专为 NAS（如 fnOS、QNAP）部署进行优化。

## ✨ 关键特性

- **OpenAI 兼容 API**：直接替代 OpenAI 接口，无需修改客户端代码。
- **智能模型路由**：根据综合评分（能力分 + 稳定分）自动选取最优模型。
- **自动供应商发现**：基于 GitHub 自动扫描免费 LLM API 并添加到系统中。
- **模型评分系统**：
  - 能力分来源于排行榜（LMSYS 等）。
  - 稳定分依据使用记录的成功率和响应延迟。
  - 综合分用于最终路由决策。
- **熔断机制**：连续失败后自动熔断该模型，避免影响整体服务。
- **流式容错**：流式请求期间若上游中断，自动切换至下一个模型，用户无感。
- **上下文压缩**：内置轻量压缩引擎，减少 Token 消耗。
- **供应商 & Key 管理**：提供完整的增删改查 API，支持多供应商、多 Key。
- **管理后台**：Web UI 可查看模型、路由组、使用统计、健康检查等信息。
- **Prometheus 监控**：内置 `/metrics` 端点，提供请求计数、延迟、活跃请求等指标。
- **限流**：基于 IP 的请求速率限制，防止滥用。
- **Docker 部署**：轻量化容器镜像，适配 NAS 环境。
- **NAS 优化**：低资源占用，适合家用 NAS 部署。

## 🏗️ 系统架构

```
客户端 (OpenAI 兼容) → Smart Gateway (FastAPI) → 多个 LLM 提供商 (FreeLLM、NVIDIA、Gemini 等)
```
- **FastAPI 服务器**：负责 API 转发、路由、限流、监控。
- **Scheduler**：后台任务调度器，定时刷新排行榜、执行供应商发现、同步评分、SLA 探测。
- **SQLite 数据库**：持久化供应商、模型、使用日志、排行榜快照、路由组等信息。
- **管理后台**：基于 HTML/JS 的 Web UI，提供一站式运维。

## 🚀 快速开始

### 前置条件
- 已安装 Docker 与 Docker Compose。
- 至少拥有一个 LLM 供应商的 API Key（如 FreeLLM、NVIDIA 等）。

### 使用 Docker Compose 部署

1. **克隆仓库**
   ```bash
   git clone https://github.com/oodop37/smart-gateway.git
   cd smart-gateway
   ```

2. **编辑配置文件** `config.yaml`（可参考 `config.example.yaml`）
   ```yaml
   port: 8765                 # 监听端口
   host: 0.0.0.0              # 绑定地址
   database:
     path: /data/smartgateway.db   # SQLite 数据库路径
   logging:
     level: INFO            # 日志等级
   scoring:
     ability_weight: 0.4    # 能力分权重
     stability_weight: 0.6  # 稳定分权重
   # 其他配置保持默认即可
   ```

3. **启动容器**
   ```bash
   docker-compose up -d
   ```

4. **检查服务**
   ```bash
   # 健康检查
   curl http://localhost:8765/health

   # 查看模型列表（OpenAI 格式）
   curl http://localhost:8765/v1/models
   ```

5. **调用 OpenAI 兼容接口**（示例）
   ```bash
   curl http://localhost:8765/v1/chat/completions \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer YOUR_GATEWAY_KEY" \
        -d '{
          "model": "auto",
          "messages": [{"role": "user", "content": "你好，今天的天气怎么样？"}],
          "max_tokens": 100
        }'
   ```

## ⚙️ 配置说明 (`config.yaml`)

```yaml
port: 8765                     # 监听端口
host: 0.0.0.0                  # 绑定地址

database:
  path: ./data/smartgateway.db # SQLite 数据库文件路径

logging:
  level: INFO                 # 日志等级：DEBUG、INFO、WARNING、ERROR

scoring:
  ability_weight: 0.4         # 能力分权重（0~1）
  stability_weight: 0.6       # 稳定分权重（0~1）

leaderboard:
  enabled: true               # 是否开启排行榜自动刷新
  interval_minutes: 360        # 刷新间隔（分钟）
  sources: []                 # 可自行添加排行榜来源

# GitHub 自动发现功能默认关闭，避免不必要的网络请求
# 如需启用请手动设置 enabled: true 并配置 interval_minutes

discovery:
  enabled: false
  interval_minutes: 1440

# SLA 探测间隔，默认每分钟一次
sla:
  interval_minutes: 1

compression:
  enabled: true               # 是否开启上下文压缩
  mode: builtin                # 压缩模式：builtin 或 rtk
  max_context_tokens: 4096      # 超出该阈值会触发压缩
  cache_ttl_minutes: 30         # 缓存有效时间（分钟）
```

> **提示**：`GATEWAY_API_KEY` 可通过环境变量或 `config.yaml` 中的 `api_key` 字段配置。若未配置，第一次启动时会自动生成默认 Key。

## 📊 管理后台

访问 `http://<host>:8765/` 即可打开 Dashboard，主要功能包括：
- **供应商 & Key 管理**：增删改查供应商及对应的 API Key。
- **模型 & 路由组**：查看模型列表，创建/编辑路由组及其排序策略。
- **使用统计**：展示最近 24h 的请求次数、成功率、Token 消耗等。
- **健康检查 & 手动刷新**：手动触发排行榜刷新、供应商发现、评分同步等任务。
- **API 信息卡**：页面顶部显示当前 API 地址、Gateway Key、默认模型（auto）等信息。

## 📈 监控与健康检查

- **健康检查**：`GET /health` 返回服务状态、启动时间、版本号以及运行时长。
- **Prometheus 指标**：`GET /metrics` 提供以下关键指标：
  - `smart_gateway_requests_total`（请求计数）
  - `smart_gateway_latency_seconds`（请求延迟）
  - `smart_gateway_active`（当前活跃请求数）
  - `smart_gateway_routing_total`（模型路由计数）

> 可将该地址加入 Grafana 或其他监控系统实时观测。

## 🐳 Docker 部署细节

- 基于 `python:3.11-slim` 镜像构建，运行时使用非 root 用户。
- 数据卷映射 `./data:/data`，持久化 SQLite 数据库。
- 如需限制资源，可在 `docker-compose.yml` 中添加 `deploy.resources` 配置。

```yaml
version: "3.8"
services:
  smart-gateway:
    build: .
    ports:
      - "8765:8765"
    volumes:
      - ./data:/data
    environment:
      - CONFIG_PATH=/app/config.yaml
    restart: unless-stopped
    # 如需限制 CPU/内存
    # deploy:
    #   resources:
    #     limits:
    #       cpus: "0.5"
    #       memory: 512M
```

## 🧪 开发与测试

```bash
# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 运行单元测试（项目已经自带部分测试）
pytest

# 本地启动服务
python app.py
```

## 📝 许可证

本项目采用 MIT 许可证，详情请参见 `LICENSE` 文件。

---

*Smart Gateway v1.0.1 已完成代码拆分、去重、Bug 修复以及中文文档编写。*