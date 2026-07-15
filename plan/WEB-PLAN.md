# 网络检索与网页抓取模块

> **状态：✅ 已实现**

## 架构概览

```
src/icoder/web/
├── __init__.py          # 公开 API：SearchEngineFactory, SearchResult, WebFetcher, WebSearchError
├── search.py            # 搜索引擎策略 + 工厂（GLM / SerpAPI）
└── fetch.py             # 网页抓取器（httpx + trafilatura → Markdown）

src/icoder/tools/
└── web_tools.py         # Agent 工具适配层（web_search / web_fetch）
```

- **搜索层** (`web/search.py`)：纯网络 I/O，与 Agent 无关，可独立单测。
- **工具层** (`tools/web_tools.py`)：负责参数校验、结果格式化、输出截断，将 `WebSearchError` / `WebFetchError` 转为 `ToolError`。
- **注册**：`create_default_registry()` 中通过 `create_web_tools()` 默认懒加载，无需 API Key 即可构建工具定义；首次调用时才按环境变量初始化引擎。

---

## 网络搜索 (`web_search`)

### 策略模式 + 工厂

| 组件 | 说明 |
|---|---|
| `SearchEngine` (ABC) | 策略接口，定义 `search(query, count) -> list[SearchResult]` |
| `GlmSearchEngine` | 智谱 GLM web_search API，端点为 `https://open.bigmodel.cn/api/paas/v4/web_search` |
| `SerpApiSearchEngine` | SerpAPI Google 搜索，端点为 `https://serpapi.com/search.json` |
| `SearchEngineFactory.create()` | 工厂方法，按 `WEB_SEARCH_PROVIDER` 环境变量选择引擎 |

### 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `WEB_SEARCH_PROVIDER` | 搜索引擎选择：`glm` / `serpapi` | `glm` |
| `GLM_API_KEY` | 智谱 API Key（与 LLM 共用） | 无（必填） |
| `GLM_WEB_SEARCH_URL` | 自定义 GLM 搜索端点 | `https://open.bigmodel.cn/api/paas/v4/web_search` |
| `SERPAPI_API_KEY` | SerpAPI Key | 无（使用 serpapi 时必填） |
| `SERPAPI_URL` | 自定义 SerpAPI 端点 | `https://serpapi.com/search.json` |

### 结果模型

```python
@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str    # 标题
    url: str      # 链接
    snippet: str  # 摘要
    source: str   # 来源站点名（如 "zhihu.com"）
```

### 工具 Schema

```json
{
  "name": "web_search",
  "parameters": {
    "type": "object",
    "properties": {
      "query":    { "type": "string", "description": "Search query." },
      "count":    { "type": "integer", "minimum": 1, "maximum": 10 },
      "provider": { "type": "string", "enum": ["glm", "serpapi"] }
    },
    "required": ["query"]
  }
}
```

- 默认返回 5 条，最多 10 条。
- 输出为编号列表：`1. [标题](URL) (来源)\n   摘要`。
- 单次输出上限 30,000 字符，超出截断并追加 `...[web output truncated]`。

---

## 网页抓取 (`web_fetch`)

### 实现细节

| 组件 | 说明 |
|---|---|
| `WebFetcher` | 使用 `httpx.stream()` 流式下载，限制超时 (20s) 与最大体积 (5MB) |
| `trafilatura` | 从 HTML 提取正文并转为 Markdown（`include_links=True`, `include_images=False`, `favor_precision=True`） |
| URL 校验 | 仅允许 `http/https`，拒绝空 URL、含凭证 URL |

### 空正文处理

按需求：若抓取的 HTML 为空或 `trafilatura` 提取不到正文，返回固定提示：

> 未提取到正文。可能是 JS 渲染或防爬墙；本期范围内不再重试

### 工具 Schema

```json
{
  "name": "web_fetch",
  "parameters": {
    "type": "object",
    "properties": {
      "url": { "type": "string", "description": "Absolute HTTP(S) page URL." }
    },
    "required": ["url"]
  }
}
```

### 错误处理

| 场景 | 行为 |
|---|---|
| 非 http/https URL | `ToolError("url must be an absolute http or https URL")` |
| 超时 | `ToolError("web fetch request timed out")` |
| HTTP 4xx/5xx | `ToolError("web fetch failed with HTTP {code}")` |
| 非 HTML Content-Type | `ToolError("unsupported content type: {type}")` |
| 超过 5MB | `ToolError("page exceeds the 5000000-byte limit")` |

---

## 依赖

```
httpx>=0.27,<1.0
python-dotenv>=1.0,<2.0
trafilatura>=2.0,<3.0
```