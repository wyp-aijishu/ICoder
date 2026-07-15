# ICoder 记忆系统实现说明

状态：已完成第一版实现。

## 模块结构

```text
src/icoder/memory/
├── __init__.py
├── manager.py       # 统一门面
├── short_term.py    # 消息历史、Token 统计、动态摘要
├── long_term.py     # 项目目录、索引、Markdown 存储、读取工具
└── extractor.py     # LLM 结构化记忆提取
```

`MemoryManager` 由 Agent 持有，统一处理短期历史、上下文压缩、长期存储和记忆提取。Agent 不维护第二份对话历史。

## 短期记忆

短期记忆保存 OpenAI-compatible 消息。一轮对话从 user 开始，到不含 `tool_calls` 的最终 assistant 消息结束。压缩按完整轮次操作，不拆分 assistant 工具调用和对应 tool 结果。

模型交互后累计服务端返回的 `total_tokens`。当前用户输入在获得 usage 前按中文约 1.5 字/token、其他文本约 4 字符/token 估算；收到真实 usage 后替换该估算。DeepSeek 最大上下文为 1,000,000 Token，GLM 为 200,000 Token。

每次调用 LLM 前检查：

$$
usedTokens \ge \lfloor maxToken \times 0.9 \rfloor
$$

达到阈值时自动压缩。`/compact` 可在完整轮次超过 3 轮时手动压缩。

压缩器提取用户目标、约束、已完成任务、关键决定、修改文件、未完成任务、错误和纠正。摘要加入第一条 system 消息的 `## 动态对话摘要` 部分。压缩后保留基础提示词、长期记忆索引、动态摘要、最近 3 轮完整对话和当前未完成轮次。再次压缩时，已有摘要和新消息合并为新的单一摘要。

## 长期记忆

### 根目录和项目隔离

记忆根目录按以下顺序确定：

1. 构造参数 `memory_root`
2. 环境变量 `ICODER_MEMORY_ROOT`
3. 默认目录 `~/.icoder/memory`

系统规范化工作区绝对路径并计算 SHA-256，作为项目目录名。Windows 下路径执行大小写归一化。

```text
<memory-root>/
└── <workspace-sha256>/
		├── MEMORY.md
		├── [user]名称.md
		├── [project]名称.md
		├── [correction]名称.md
		└── [resource]名称.md
```

### 类型和文件格式

| 类型 | 含义 |
|---|---|
| `user` | 用户稳定偏好、习惯或长期要求 |
| `project` | 项目架构、命令、约定或稳定事实 |
| `correction` | 用户对错误操作或不符合要求行为的纠正 |
| `resource` | 外部文档、接口或资源信息 |

单条记忆格式：

```markdown
---
type: project
name: "测试命令"
description: "项目使用 pytest 运行自动化测试"
---

## Content

运行完整测试时使用 python -m pytest -q。
```

文件名格式为 `[type]名称.md`。非法文件名字符替换为 `-`，名称最多 80 字符，description 最多 300 字符，content 最多 24,000 字符。同名、同类型记忆更新原文件。文件和索引通过临时文件替换方式写入，并由进程内可重入锁保护。

### 索引和加载

`MEMORY.md` 每行格式：

```text
[project]测试命令.md: 项目使用 pytest 运行自动化测试
```

启动时只读取前 200 行并加入 system prompt。新项目自动创建项目目录和空索引。

Agent 注册 `read_memory` 工具。它只能读取当前项目已加载索引中的准确文件名，拒绝绝对路径、父目录跳转和目录逃逸，最多返回 24,000 字符。模型没有直接写长期记忆的工具权限。

## 记忆写入

### 显式写入

```text
/save 用户偏好简洁的中文回答
```

`/save` 内容不进入普通对话历史。当前 LLM 将内容提取为严格 JSON，代码校验后最多保存 3 条记忆，并立即刷新当前会话 system prompt 中的索引。

### 隐式写入

user 消息、assistant 工具调用、每条 tool 结果和 assistant 最终回复各计为一个事件。事件数达到 4 后，在最终回复完成时：

1. 复制并清空待处理事件。
2. 启动守护后台线程。
3. 创建同 Provider、同模型的独立 LLM Client。
4. 提取最多 3 条有价值记忆并写入文件和索引。

后台异常不影响主对话。隐式保存会更新磁盘索引和可读索引；为避免异步改写当前消息，完整索引在下一次 ICoder 启动时注入 system prompt。

## 提取格式与安全

LLM 输出格式：

```json
{
	"memories": [
		{
			"type": "project",
			"name": "测试命令",
			"description": "项目使用 pytest 运行测试",
			"content": "运行完整测试时使用 python -m pytest -q。"
		}
	]
}
```

没有值得保存的信息时返回 `{"memories":[]}`。提取提示禁止保存 API Key、密码、Token、Cookie、私钥等秘密，也不保存闲聊、一次性任务或未经确认的推测。

## 命令行为

| 命令 | 行为 |
|---|---|
| `/compact` | 压缩较早轮次，保留最近 3 轮 |
| `/save 内容` | 同步提取并保存长期记忆 |
| `/clear` | 清除短期历史、摘要、Token 计数和待提取事件，不删除长期记忆 |

## 当前边界

- 长期记忆使用本地 Markdown 文件，暂不支持向量检索或语义搜索。
- 后台失败任务不持久化，也不自动重试。
- 锁只保证当前进程内安全，不处理多个 ICoder 进程同时写同一项目。
- 隐式写入不会异步改写当前短期 system 消息；完整索引在下一次启动时加载。