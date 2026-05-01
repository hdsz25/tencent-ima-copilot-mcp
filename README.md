# IMA Copilot MCP 服务器

基于 FastMCP v2 框架的腾讯 IMA Copilot MCP (Model Context Protocol) 服务器，**使用环境变量配置**，简化项目结构，专注于 MCP 协议实现。

## ✨ 主要特性

- 🚀 **极简配置**: 仅需两个必需参数即可启动，开箱即用
- 🤖 **MCP 协议支持**: 完整实现 Model Context Protocol 规范 (基于 FastMCP 2.14.1)
- 🔧 **环境变量配置**: 通过 `.env` 文件管理所有配置
- 📡 **HTTP 传输**: 支持 HTTP 传输协议，便于 MCP Inspector 连接
- 🛠️ **增强型 MCP 工具**: 提供腾讯 IMA 知识库问答功能，返回结果包含回答文本和结构化参考资料
- 🗂️ **知识库目录同步**: 自动拉取个人/共享知识库名称与 ID，并持久化到本地配置文件
- 🧠 **自动选库与多候选融合**: `ask(question)` 会自动匹配多个候选知识库，并融合最相关的回答
- 📚 **分组参考资料**: 合并后的参考资料会按知识库来源分组展示，并过滤明显不相关的条目
- 🔄 **Token 自动刷新**: 智能管理认证 token，自动刷新保持会话有效
- 💪 **Tenacity-powered Retries**: 集成 tenacity 库，优化重试机制，支持指数退避和针对性错误重试
- 🧯 **Code=3 自愈**: 对高并发瞬时 `Code=3` 错误执行退避重试并自动恢复
- 🚦 **并发限流**: 默认低并发问答（并发=2），兼顾多候选检索与系统稳定性
- 📝 **Loguru-enhanced Logging**: 采用 Loguru 提升日志体验，提供更清晰、结构化的日志输出
- ⏱️ **超时保护**: 内置请求超时机制，防止长时间阻塞 (已提升至 300 秒)
- 🎯 **一键启动**: 简化的启动流程，自动环境检查和配置验证
- 🐳 **Docker 支持**: 提供官方 Docker 镜像，开箱即用

## 🚀 最新进展 (2026-05)

- ✅ **服务器成功运行**: 已验证基于 FastMCP 2.14.1 的 HTTP 传输模式正常工作。
- ✅ **Ollama 本地集成支持**: 支持通过 `OLLAMA_HOST` 连接本地 `embeddinggemma` 进行向量嵌入。
- ✅ **ChromaDB 向量持久化**: 支持在本地挂载 `./chromadb`，以长期保留向量数据，避免重启容器后重复生成。
- ✅ **主机网络直接通讯**: 默认提供 `network_mode: "host"`，使得 Docker 容器直接共享宿主机网络，安全且高效地访问受保护的本地大模型（如 `127.0.0.1:11434`）。
- ✅ **按需日志输出**: 默认的 `IMA_MCP_LOG_LEVEL` 调整为 `ERROR`，减少无意义日志噪音。
- ✅ **修复大模型客户端无参调用错误**: `sync_knowledge_bases` 工具现在能够兼容大模型传入的冗余的 `random_string` 或 `**kwargs` 参数，避免 Pydantic 校验报错。

## 快速开始

### 方式一：使用 Docker（推荐）

#### 1. 使用 Docker Run

```bash
# 拉取镜像
docker pull highkay/tencent-ima-copilot-mcp:latest

# 运行容器（需要替换以下两个必需的环境变量）
docker run -d \
  --name ima-copilot-mcp \
  --network host \
  -e IMA_X_IMA_COOKIE="your_x_ima_cookie_here" \
  -e IMA_X_IMA_BKN="your_x_ima_bkn_here" \
  -e IMA_KNOWLEDGE_BASE_CATALOG_FILE="/app/data/.ima_knowledge_bases.json" \
  -e OLLAMA_HOST="http://127.0.0.1:11434" \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/chromadb:/app/chromadb \
  --restart unless-stopped \
  highkay/tencent-ima-copilot-mcp:latest

# 查看日志
docker logs -f ima-copilot-mcp
```

#### 2. 使用 Docker Compose（更便捷）

如果使用本地 Ollama 进行向量支持，`docker-compose.yml` 已经默认开启 `network_mode: "host"` ，容器将自动连接 `http://127.0.0.1:11434`。

创建 `.env` 文件（或直接在 shell 中设置环境变量）：

```bash
# .env 文件
IMA_X_IMA_COOKIE="your_x_ima_cookie_here"
IMA_X_IMA_BKN="your_x_ima_bkn_here"
IMA_KNOWLEDGE_BASE_CATALOG_FILE="/app/data/.ima_knowledge_bases.json"
OLLAMA_HOST="http://127.0.0.1:11434"
```

> **注意：** 挂载的 `./chromadb`, `./logs`, `./data` 需要对 Docker 拥有写入权限。如果无法写入，可用 `sudo chown -R $USER:$USER ./chromadb ./logs ./data` 将所有权拿回本地账户。

启动服务：

```bash
# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### 方式二：本地安装

#### 1. 安装依赖

```bash
# 安装 FastMCP、tenacity、Loguru 和所有依赖
pip install -r requirements.txt
```

#### 2. 配置环境变量

```bash
# 复制配置文件模板
cp .env.example .env

# 编辑 .env 文件，填入从浏览器获取的 IMA 认证信息
nano .env  # 或使用其他编辑器
```

#### 必需配置项

以下环境变量必须正确配置才能使用服务：

- **`IMA_X_IMA_COOKIE`**: X-Ima-Cookie 请求头值（包含平台信息、token 等）
- **`IMA_X_IMA_BKN`**: X-Ima-Bkn 请求头值（业务密钥）

#### 3. 获取 IMA 认证信息

#### 步骤 1: 访问 IMA Copilot

1. 访问 [https://ima.qq.com](https://ima.qq.com) 并登录
2. 按 F12 打开开发者工具
3. 切换到 **Network** (网络) 标签页

#### 步骤 2: 获取认证头信息

1. 在 IMA 中发送一条消息
2. 找到向 `/cgi-bin/assistant/qa` 的 POST 请求
3. 查看 **Request Headers**，复制以下字段：
   - `x-ima-cookie` → `IMA_X_IMA_COOKIE`
   - `x-ima-bkn` → `IMA_X_IMA_BKN`

#### 4. 启动服务器

##### 方式一：使用启动脚本（推荐）

```bash
# Windows
start.bat

# 或使用 Python 脚本（跨平台）
python run.py
```

##### 方式二：使用 fastmcp 命令

```bash
fastmcp run ima_server_simple.py:mcp --transport http --host 127.0.0.1 --port 8081
```

#### 5. 使用 MCP Inspector

```bash
# 安装 MCP Inspector
npx @modelcontextprotocol/inspector

# 连接到服务器
# 在 Inspector 中输入: http://127.0.0.1:8081/mcp
```

### 服务端点

- **MCP 协议端点**: `http://127.0.0.1:8081/mcp`（用于 MCP Inspector 或其他 MCP 客户端）
- **日志文件**: `logs/debug/ima_server_YYYYMMDD_HHMMSS.log`（Loguru 自动生成和管理）
- **原始 SSE 日志**: `logs/debug/raw/sse_*.log`（发生错误时自动保存）

## 可用的 MCP 工具

### 1. `ask`

向腾讯 IMA 知识库询问任何问题

**参数:**
- `question` (必需): 要询问的问题

**示例:**
```
问题: "什么是机器学习？"
问题: "如何制作番茄炒蛋？"
```

**特性:**
- 自动管理会话，无需手动创建
- 智能 token 刷新，确保认证有效
- 内置并发限流（默认 `IMA_ASK_CONCURRENCY_LIMIT=2`）
- 检测到 `Code=3` 且无文本时自动指数退避重试（最多 2 次）
- **300 秒超时保护**，防止长时间等待
- 多知识库模式下会自动选择多个候选知识库并融合结果
- 返回内容为 **`TextContent` 列表**，包含**融合后的回答文本**和按知识库分组后的**参考资料**

> 注意：如果尚未同步知识库目录，`ask` 会先尝试使用本地目录；必要时请先执行 `sync_knowledge_bases`。

### 2. `ask_with_kb`

向指定知识库询问问题（多知识库模式）

**参数:**
- `question` (必需): 要询问的问题
- `knowledge_base_id` (必需): 目标知识库 ID（必须在配置列表中）

**示例:**
```
问题: "总结这个知识库的核心内容"
knowledge_base_id: "7305806844290061"
```

### 3. `sync_knowledge_bases`

同步 IMA 个人/共享知识库目录，并写入本地目录文件。

**效果:**
- 自动发现当前账号可见的个人/共享知识库
- 持久化 `id -> name` 映射到 `IMA_KNOWLEDGE_BASE_CATALOG_FILE`
- 自动回写 `.env` 中的 `IMA_KNOWLEDGE_BASE_ID` 和 `IMA_KNOWLEDGE_BASE_IDS`

## 可用的 MCP 资源

### 1. `ima://config`

获取当前配置信息（不包含敏感数据）

### 2. `ima://help`

获取帮助信息

## 配置选项

### 必需的环境变量

| 变量名 | 说明 | 获取方式 |
|--------|------|---------|
| `IMA_X_IMA_COOKIE` | X-Ima-Cookie 请求头 | 从浏览器开发者工具 Network 标签中复制 |
| `IMA_X_IMA_BKN` | X-Ima-Bkn 请求头 | 从浏览器开发者工具 Network 标签中复制 |

### 可选的环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `IMA_KNOWLEDGE_BASE_ID` / `knowledgeBaseId` | 单知识库 ID（两者等价） | 无 |
| `IMA_KNOWLEDGE_BASE_IDS` / `knowledgeBaseIds` | 多知识库 ID 列表（逗号分隔） | 无 |
| `IMA_KNOWLEDGE_BASE_CATALOG_FILE` / `knowledgeBaseCatalogFile` | 知识库目录文件路径 | `.ima_knowledge_bases.json` |
| `IMA_MCP_HOST` | MCP 服务器地址 | `127.0.0.1` |
| `IMA_MCP_PORT` | MCP 服务器端口 | `8081` |
| `IMA_MCP_LOG_LEVEL` | 日志级别 (支持 `DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `IMA_REQUEST_TIMEOUT` | IMA API 请求超时时间（秒） | `30` |
| `IMA_RETRY_COUNT` | 网络/超时类异常重试次数 | `3` |
| `IMA_ASK_CONCURRENCY_LIMIT` | 问答并发上限（建议 2-3） | `2` |
| `IMA_ROBOT_TYPE` | 机器人类型 | `5` |
| `IMA_SCENE_TYPE` | 场景类型 | `1` |
| `IMA_MODEL_TYPE` | 模型类型 | `4` |

### 知识库配置模式

- 单知识库模式：配置 `IMA_KNOWLEDGE_BASE_ID`（或 `knowledgeBaseId`），`ask` 和 `ask_with_kb` 都可以直接使用。
- 多知识库模式：配置 `IMA_KNOWLEDGE_BASE_IDS`（或 `knowledgeBaseIds`，逗号分隔），`ask` 会自动选库并融合候选结果，`ask_with_kb` 可用于强制指定单库。
- 认证优先模式：只配置 `IMA_X_IMA_COOKIE` 和 `IMA_X_IMA_BKN` 也可以启动；首次执行 `sync_knowledge_bases` 后会自动同步知识库目录。
- 同时配置单库和多库时：优先使用 `IMA_KNOWLEDGE_BASE_ID` 作为默认库，但 `ask` 仍会优先参考已同步目录进行自动匹配。

### 自动同步与自动选库

1. 启动服务后先执行 `sync_knowledge_bases`
2. 服务会从 `https://ima.qq.com/wikis` 对应接口拉取个人/共享知识库清单
3. 同步结果会保存知识库 `id -> name` 映射，后续 `ask(question)` 会根据问题和知识库名称的相关性自动选择多个候选知识库
4. 服务会并发检索候选知识库，融合最相关的回答，并输出按知识库来源分组的参考资料

### Docker 持久化建议

- 建议为 `IMA_KNOWLEDGE_BASE_CATALOG_FILE` 指定容器内持久化路径，例如 `/app/data/.ima_knowledge_bases.json`
- 建议挂载 `./data:/app/data`，这样 `sync_knowledge_bases` 生成的目录文件在容器重建后仍会保留
- 如果希望容器内自动回写的 `.env` 也保留到宿主机，可额外挂载项目根目录下的 `.env` 到 `/app/.env`

### 从旧版本迁移

- 只用一个知识库：保持原有 `IMA_KNOWLEDGE_BASE_ID=<id>` 即可，无需改调用方式。
- 需要多个知识库：新增 `IMA_KNOWLEDGE_BASE_IDS=id1,id2,...`，并把调用从 `ask(question)` 改为 `ask_with_kb(question, knowledge_base_id)`。

## 故障排除

### 常见问题

**Q: 认证失败（Token 验证失败）怎么办？**

A:
1. 检查 `.env` 文件中的 `IMA_X_IMA_COOKIE` 和 `IMA_X_IMA_BKN` 是否正确
2. 确认 `IMA_X_IMA_COOKIE` 中包含 `IMA-REFRESH-TOKEN` 字段
3. 重新从浏览器获取最新的认证信息

**Q: 如何连接特定的知识库？**

A:
在 `.env` 文件中设置 `IMA_KNOWLEDGE_BASE_ID`（或 `knowledgeBaseId`）即可。获取方法：
1. 在 IMA 网页选择知识库
2. 找到 `init_session` 请求
3. 查看 Payload 中的 `knowledge_base_id`

**Q: 多知识库怎么配置和调用？**

A:
1. 最简单的方式是只配置 `IMA_X_IMA_COOKIE` 和 `IMA_X_IMA_BKN`
2. 启动后执行 `sync_knowledge_bases`
3. 后续直接使用 `ask(question)`，服务会自动选库、并发检索候选库，并融合回答
4. 如果要强制指定某个库，再使用 `ask_with_kb(question, knowledge_base_id)`

A:
1. 在 `.env` 中设置 `IMA_KNOWLEDGE_BASE_IDS=id1,id2,id3`
2. 调用工具时使用 `ask_with_kb(question, knowledge_base_id)`
3. 若调用 `ask`，会提示错误并给出可用 `knowledge_base_id` 列表

**Q: 偶发出现 `Code=3` 且无文本怎么办？**

A:
1. 先保持默认并发（`IMA_ASK_CONCURRENCY_LIMIT=1`）
2. 避免同一知识库短时间突发并发请求
3. 服务已内置 `Code=3` 退避重试；若仍频繁出现，可适当增加请求间隔

## 许可证

MIT License
