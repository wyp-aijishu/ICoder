# ICoder stdio MCP 与 Chrome DevTools 接入计划

## 1. 目标

在不异步化现有 Agent、LLM 和 Tool 调用链的前提下，为 ICoder 增加通用的本地 stdio MCP 客户端能力，并以 Google 官方 Chrome DevTools MCP 作为首个配置和验收服务。

首版目标：

- 支持通过 stdio 启动和连接本地 MCP Server。
- 支持 MCP 初始化、工具发现和工具调用。
- 将远端 MCP Tool 动态注册到现有 `ToolRegistry`。
- 通过用户级 `~/.icoder/mcp.json` 管理 MCP Server。
- 默认使用隔离 Chrome profile，避免暴露日常浏览数据。
- 单个 MCP Server 启动失败时警告并继续运行 ICoder。
- 保持现有同步 ReAct 调用链和内置工具行为不变。
- 首版只向模型返回文本和结构化 JSON，不传递图片、音频或二进制内容。

## 2. 当前架构与接入点

ICoder 当前工具调用链：

```text
Agent
	-> ToolRegistry.definitions()
	-> LLM tool_calls
	-> ToolRegistry.execute()
	-> Tool.handler(arguments)
	-> ToolResult
```

当前 `Tool.handler` 是同步函数，而 MCP Python SDK 的 stdio Client 是异步接口。因此接入层采用以下方式：

```text
同步 Agent / ToolRegistry
					|
					| run_coroutine_threadsafe
					v
McpRuntime 后台线程 + asyncio event loop
					|
					v
MCP ClientSession
					|
					v
stdio 子进程：chrome-devtools-mcp
```

不自行实现 JSON-RPC 和 stdio framing。使用官方 Python MCP SDK 处理协议协商、消息解析、Windows 命令解析和子进程树清理。

## 3. 技术决策

### 3.1 MCP SDK

使用官方 Python MCP SDK 稳定 v1：

```toml
mcp>=1.28,<2
```

不使用 v2 预发布版，避免其 API 和协议支持继续发生破坏性变化。

### 3.2 配置作用域

首版只读取用户级配置：

```text
~/.icoder/mcp.json
```

不读取或合并项目级 MCP 配置。

### 3.3 Chrome 模式

默认示例使用 `--isolated`：

- 每次会话使用临时 Chrome profile。
- MCP/Chrome 关闭后自动清理 profile。
- 不访问用户日常 Chrome profile、Cookie 和登录状态。

### 3.4 故障策略

- 配置文件不存在：视为未配置 MCP，正常启动。
- 配置文件非法：显示警告，继续使用内置工具。
- 单个 MCP Server 失败：显示带服务名的警告，继续启动其他服务。
- MCP Tool 调用失败：通过现有 `ToolResult.is_error` 回灌给模型。

### 3.5 首版内容类型

支持：

- `TextContent`
- `structuredContent`
- 文本型 `EmbeddedResource`

暂不支持：

- `ImageContent`
- 音频内容
- 二进制 Embedded Resource

遇到不支持的内容时返回明确错误，不把 base64 数据写入模型上下文。浏览器页面结构读取优先使用 `take_snapshot`，不依赖 `take_screenshot`。

## 4. 实施阶段

## Phase 1：依赖与配置

### 4.1 增加 MCP SDK 依赖

修改：

- `pyproject.toml`
- `requirements.txt`

增加 `mcp>=1.28,<2`，并保持 Python 3.11 兼容。

### 4.2 实现用户级 MCP 配置加载器

新增：

```text
src/icoder/mcp/config.py
```

采用兼容 Claude Desktop 的配置结构：

```json
{
	"mcpServers": {
		"chrome-devtools": {
			"command": "npx",
			"args": [
				"-y",
				"chrome-devtools-mcp@latest",
				"--isolated",
				"--no-usage-statistics",
				"--no-performance-crux"
			],
			"env": {},
			"enabled": true,
			"startupTimeoutSeconds": 60,
			"toolTimeoutSeconds": 120
		}
	}
}
```

配置模型需要校验：

- `mcpServers` 必须是对象。
- Server 名称不能为空。
- `command` 必须是非空字符串。
- `args` 必须是字符串数组。
- `env` 必须是字符串到字符串的映射。
- `enabled` 必须是布尔值。
- 超时必须是大于零的数值。

支持以下变量展开：

```text
${HOME}
${VAR}
```

环境变量缺失时，将对应 Server 标记为配置错误，不把未展开占位符传给子进程。错误消息不得打印环境变量值，避免泄露秘密。

## Phase 2：MCP Runtime 与生命周期

### 4.3 实现后台 MCP Runtime

新增：

```text
src/icoder/mcp/runtime.py
```

实现 `McpRuntime`，职责包括：

- 创建专用后台线程。
- 在线程中运行长期存活的 asyncio event loop。
- 为每个 Server 创建 `StdioServerParameters`。
- 使用 `stdio_client()` 启动 stdio 子进程。
- 创建并持有 `ClientSession`。
- 调用 `initialize()` 完成协议协商。
- 调用 `list_tools()` 获取工具定义。
- 调用 `call_tool()` 执行远端工具。
- 关闭时退出 Session 和 stdio context。
- 停止事件循环并等待后台线程退出。

每个 Server 的连接状态相互隔离，一个 Server 失败不能关闭其他已成功连接的 Server。

### 4.4 暴露 workspace roots

创建 `ClientSession` 时提供 `list_roots_callback`，将当前 workspace 暴露为 MCP root。

这样 Chrome DevTools MCP 的文件型工具可遵守客户端 root，不需要默认启用高风险参数：

```text
--allow-unrestricted-paths
```

### 4.5 同步调用门面

为现有同步工具链提供：

```python
runtime.start()
runtime.call_tool(server_name, tool_name, arguments)
runtime.close()
```

同步方法通过 `asyncio.run_coroutine_threadsafe()` 把任务提交到后台事件循环。

超时分为：

- MCP Server 启动和初始化超时。
- 单次 Tool 调用超时。

`close()` 必须幂等，并覆盖以下状态：

- 尚未启动。
- 全部启动成功。
- 部分 Server 启动成功。
- 初始化超时。
- 子进程提前退出。
- Tool 调用期间退出。

SDK 负责执行标准 stdio 关闭序列：

1. 关闭 Server stdin。
2. 等待 Server 自行退出。
3. 超时后终止进程树。
4. 仍未退出时强制终止。

## Phase 3：MCP Tool 桥接

### 4.6 实现工具桥接器

新增：

```text
src/icoder/mcp/bridge.py
```

将 MCP `tools/list` 返回的 Tool 转换为现有 `Tool` 对象。

公开工具名格式：

```text
mcp__<server>__<tool>
```

例如：

```text
mcp__chrome_devtools__new_page
mcp__chrome_devtools__take_snapshot
mcp__chrome_devtools__list_console_messages
```

命名规则：

- 非字母、数字和下划线字符转换为下划线。
- 闭包内部保留原始 Server 名和 MCP Tool 名。
- 检测规范化后的工具名冲突。
- 不允许覆盖内置工具。

### 4.7 Schema 转换

- 保留合法 `inputSchema` 的对象结构。
- 移除 `$schema` 等不影响参数语义的元数据字段。
- 不擅自删除 `$ref`、`anyOf`、`oneOf` 等约束。
- 没有描述时生成稳定的默认描述。
- 非法或非对象 Schema 视为该 MCP Tool 无法注册，并记录警告。

只有在真实 Provider 验证表明确认复杂 Schema 不兼容后，才增加独立的 Schema 降级策略。

### 4.8 Tool 结果转换

结果转换规则：

- 多个文本块按顺序拼接。
- `structuredContent` 使用稳定 UTF-8 JSON 序列化。
- 避免把与文本内容完全重复的结构化结果再次写入上下文。
- Server 返回 `isError=true` 时抛出 `ToolError`。
- 图片、音频和二进制资源返回明确的不支持错误。
- 调用超时、Server 退出和协议错误转换为可读的 `ToolError`。

新增：

```text
src/icoder/mcp/__init__.py
```

只导出 CLI 装配所需的配置、Runtime、桥接函数和错误类型，避免 CLI 直接依赖 SDK 内部实现。

## Phase 4：CLI 与 Agent 装配

### 4.9 CLI 启动流程

修改：

```text
src/icoder/cli/main.py
```

启动顺序：

1. 验证 workspace。
2. 创建 LLM Client。
3. 创建内置 `ToolRegistry`。
4. 加载 `~/.icoder/mcp.json`。
5. 启动配置中启用的 MCP Server。
6. 获取并注册 MCP Tools。
7. 创建 Agent。
8. 进入 REPL。

启动信息需要显示：

- MCP 配置文件路径。
- 成功连接的 Server 名称。
- 每个 Server 发现的工具数量。
- 启动或工具发现失败的警告。

### 4.10 保证退出清理

使用 `try/finally` 覆盖 REPL 的全部退出路径：

- `/exit`
- `/quit`
- EOF
- 启动后的异常
- 用户中断后最终退出

`finally` 中调用 `runtime.close()`，确保不遗留 `npx`、`chrome-devtools-mcp` 和 Chrome 子进程。

保留现有 `client_factory` 和 `registry_factory` 注入点，同时增加：

- MCP config loader 注入点。
- MCP runtime factory 注入点。

单元测试不得启动真实 MCP 子进程。

### 4.11 动态工具提示

修改：

```text
src/icoder/agent/agent.py
```

当前 system prompt 固定列出了少量内置工具，无法描述动态 MCP Tool。调整为：

- 使用通用工具调用政策。
- 或根据 Registry definitions 生成简洁工具清单。
- 明确 `mcp__` 前缀表示外部 MCP 工具。
- 浏览器页面分析优先使用 `take_snapshot`。
- 首版不诱导模型使用 `take_screenshot` 读取页面。

## Phase 5：测试

### 4.12 配置测试

新增：

```text
tests/unit/mcp/test_config.py
```

覆盖：

- 配置文件不存在。
- 合法 Chrome DevTools 配置。
- `enabled=false`。
- 用户级默认路径。
- 环境变量展开。
- 环境变量缺失。
- 非法 JSON。
- 非法字段类型。
- 非法超时。
- 错误消息不泄露 env 内容。

### 4.13 Runtime 测试

新增：

```text
tests/unit/mcp/test_runtime.py
```

通过注入 fake async connector/session 验证：

- `initialize -> list_tools -> call_tool` 顺序。
- workspace roots。
- 启动超时。
- Tool 调用超时。
- 单 Server 故障隔离。
- 部分启动成功后的清理。
- 幂等关闭。
- 后台线程最终退出。

测试不得运行 `npx`，不得访问网络。

### 4.14 Bridge 测试

新增：

```text
tests/unit/mcp/test_bridge.py
```

覆盖：

- 工具命名空间。
- 原始工具名映射。
- 工具名冲突。
- Schema 转换。
- 文本结果。
- structured result。
- 文本 Embedded Resource。
- Server `isError`。
- 图片和二进制内容拒绝。
- MCP 错误进入 `ToolRegistry` 后转换为标准 `ToolResult`。

### 4.15 CLI 和 Agent 回归测试

扩展：

```text
tests/unit/cli/test_main.py
tests/unit/tools/test_registry.py
tests/unit/agent/
```

覆盖：

- MCP 成功注册。
- MCP 启动失败时警告并继续。
- 未配置 MCP 时行为不变。
- `/exit` 和 EOF 都会关闭 Runtime。
- 动态 MCP Tool 对模型可见。
- 内置 Tool 数量和行为不受影响。

## Phase 6：文档

### 4.16 更新 README

修改：

```text
README.md
```

补充：

- Node.js LTS、npm、当前稳定版 Chrome 环境要求。
- `~/.icoder/mcp.json` 配置格式。
- Chrome DevTools MCP 推荐隔离配置。
- `--headless` 和 `--slim` 用法。
- 连接现有 Chrome 的 `--autoConnect` 和 `--browser-url` 替代方案。
- Windows 下的启动说明。
- Chrome MCP 可读取、调试和修改浏览器内容的安全警告。
- Google usage statistics 和 CrUX 数据行为及关闭参数。
- 首版不支持图片 Tool 结果的边界。

不要在安装或启动过程中自动创建、覆盖用户真实的 `~/.icoder/mcp.json`。

## 5. 推荐 Chrome DevTools 配置

用户级配置文件：

```text
C:\Users\<用户名>\.icoder\mcp.json
```

推荐内容：

```json
{
	"mcpServers": {
		"chrome-devtools": {
			"command": "npx",
			"args": [
				"-y",
				"chrome-devtools-mcp@latest",
				"--isolated",
				"--no-usage-statistics",
				"--no-performance-crux"
			],
			"startupTimeoutSeconds": 60,
			"toolTimeoutSeconds": 120
		}
	}
}
```

只需要基础浏览器功能时可使用：

```json
{
	"mcpServers": {
		"chrome-devtools": {
			"command": "npx",
			"args": [
				"-y",
				"chrome-devtools-mcp@latest",
				"--isolated",
				"--headless",
				"--slim",
				"--no-usage-statistics"
			]
		}
	}
}
```

不建议首版默认连接用户日常 Chrome。若后续手工启用 `--autoConnect` 或 `--browser-url`，必须明确提示 MCP Server 将能够访问和修改该 Chrome profile 中打开的页面及数据。

## 6. 自动化验证

先运行聚焦测试：

```powershell
python -m pytest -q tests/unit/mcp tests/unit/cli/test_main.py tests/unit/tools/test_registry.py
```

再运行完整验证：

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m pip check
python -m pip wheel . --no-deps
```

验收标准：

- 新增单元测试全部通过。
- 原有 Agent、CLI、Memory、LLM 和 Tool 测试无回归。
- Wheel 构建包含 `icoder.mcp` 包。
- 未配置 MCP 时启动行为与当前版本一致。

## 7. Windows 手工冒烟测试

### 7.1 环境检查

```powershell
node --version
npm --version
npx --version
```

确认已安装当前稳定版 Google Chrome。

### 7.2 工具发现

创建 `~/.icoder/mcp.json` 后启动：

```powershell
icoder --workspace .
```

确认 CLI 显示：

- `chrome-devtools` 连接成功。
- 已发现 MCP Tool。
- Tool 名使用 `mcp__chrome_devtools__...` 格式。

### 7.3 页面与控制台测试

向 Agent 输入：

```text
打开 https://developers.chrome.com，读取页面快照，并列出控制台错误。
```

确认实际调用：

- `new_page`
- `take_snapshot`
- `list_console_messages`

### 7.4 性能测试

向 Agent 输入：

```text
检查 https://developers.chrome.com 的页面性能并给出主要问题。
```

确认：

- Chrome 在首次需要浏览器的 Tool 调用时才启动。
- Performance trace 可以完成。
- 文本性能结果可以返回模型。

### 7.5 生命周期测试

退出 ICoder 后检查任务管理器：

- 没有遗留 `chrome-devtools-mcp` 进程。
- 没有遗留由本次 MCP 会话启动的 `npx` 进程。
- 隔离 Chrome 实例已退出。
- 临时 Chrome profile 已清理。

### 7.6 故障降级测试

把 `command` 临时改成不存在的命令，确认：

- CLI 显示带 Server 名的启动警告。
- ICoder 仍然进入 REPL。
- 内置文件、命令和搜索工具仍可使用。
- 退出过程不挂起。

### 7.7 多模态边界测试

调用 `take_screenshot`，确认：

- 返回明确的首版多模态不支持错误。
- Tool 结果中没有大段 base64。
- 改用 `take_snapshot` 后任务可继续执行。

## 8. 首版不包含

以下能力不纳入本次实现：

- Streamable HTTP 和 SSE Transport。
- MCP Resource 和 Prompt 暴露。
- `tools/list_changed` 动态热更新。
- MCP Server 自动重启。
- 配置文件热重载。
- `/mcp list`、`/mcp restart` 等管理命令。
- 项目级 MCP 配置。
- Human-in-the-Loop 工具审批。
- 浏览器敏感页面策略和 URL Guard。
- DeepSeek/GLM 图片多模态消息适配。
- 并发 Agent 共享浏览器页面路由。

这些能力应在 stdio MCP 核心链路稳定后独立设计和实施。

## 9. 预期文件变更

```text
pyproject.toml
requirements.txt
README.md

src/icoder/mcp/__init__.py
src/icoder/mcp/config.py
src/icoder/mcp/runtime.py
src/icoder/mcp/bridge.py

src/icoder/cli/main.py
src/icoder/agent/agent.py

tests/unit/mcp/__init__.py
tests/unit/mcp/test_config.py
tests/unit/mcp/test_runtime.py
tests/unit/mcp/test_bridge.py

tests/unit/cli/test_main.py
tests/unit/tools/test_registry.py
tests/unit/agent/
```

是否需要修改 `src/icoder/tools/base.py`，应由结果错误映射的实际实现决定。首版不在该文件引入多模态结果模型。

## 10. 完成定义

满足以下条件后，本阶段完成：

1. ICoder 能从用户级配置启动任意 stdio MCP Server。
2. MCP Tool 能动态注册并经过现有 ReAct 循环调用。
3. Chrome DevTools MCP 的页面、DOM、控制台、网络和性能文本工具可用。
4. Server 启动失败不会阻断 ICoder 内置能力。
5. 所有退出路径均能关闭 MCP 子进程树。
6. 图片和二进制结果不会进入模型上下文。
7. 自动化测试完全离线并通过。
8. Windows Chrome DevTools MCP 手工冒烟测试通过。
