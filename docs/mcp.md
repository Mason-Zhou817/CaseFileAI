# CaseFile AI MCP（本地 stdio）

CaseFile AI MCP 让支持 Model Context Protocol 的智能体通过进程调用现有案件服务。它复用 GUI / CLI 的案件、导入、索引、证据约束问答、Claim、矛盾和导出服务，不提供远程网络监听。MCP 客户端必须能在本机启动子进程。

## 启动与客户端配置

安装项目后运行：

```text
casefile-ai-mcp
```

开发环境使用 `uv run casefile-ai-mcp`。服务以 stdio 传输 JSON-RPC；stdout 仅用于协议数据，安全日志写入应用日志文件。客户端断开后进程退出，Ctrl+C 可停止手动启动的服务。

通用 MCP 客户端配置：

```json
{
  "mcpServers": {
    "casefile-ai": {
      "command": "casefile-ai-mcp",
      "args": [],
      "env": {
        "CASEFILE_MCP_IMPORT_ROOTS": "C:\\CaseFileAI-Imports"
      }
    }
  }
}
```

Windows 开发模式也可将 `command` 设为 `uv`，`args` 设为 `["run", "casefile-ai-mcp"]`，并在客户端设置项目工作目录。配置中的导入目录是示例，实际使用前需自行创建。标准 MCP Client 会先执行 `initialize` 和 `tools/list`，再调用工具。

## 工具

| 工具 | 作用 | 可能调用已配置云模型 |
| --- | --- | --- |
| `list_cases` | 列出案件 | 否 |
| `create_case` | 创建案件 | 否 |
| `list_documents` | 列出指定案件材料版本 | 否 |
| `import_document` | 从允许目录导入 PDF | 否 |
| `build_index` | 为案件建立或更新本地索引 | 否 |
| `get_index_status` | 读取案件索引状态 | 否 |
| `search_evidence` | 检索案件证据及来源 | 否 |
| `ask_case` | 证据约束问答及引用验证 | 是 |
| `extract_claims` | 从指定案件的已验证 Answer 抽取 Claim | 是 |
| `list_claims` | 列出已保存的 Claim | 否 |
| `run_contradiction_analysis` | 运行矛盾判断 | 行为、主体语义判断时可能调用 |
| `list_contradictions` | 列出已保存的矛盾结果 | 否 |
| `get_timeline` | 从已保存 Claim 读取时间线 | 否 |
| `export_case_analysis` | 直接返回 JSON / Markdown 分析内容 | 否 |
| `get_system_status` | 返回脱敏健康状态，可选案件索引状态 | 否 |

本地索引和检索可能需要首次下载或加载本地嵌入模型；扫描件导入可能需要本地 OCR。问答、Claim 和部分矛盾判断只有在用户明确配置并启用模型调用后才可能外发。MCP 客户端本身也可能把工具结果送给其所用模型；请检查该客户端的数据处理方式，并仅使用合成或人工脱敏材料。

## 输入与结构化结果

所有案件材料、证据、Answer、Claim、矛盾和时间线工具均要求 `case_id`，Answer ID 会经服务层验证归属。检索结果包含文档显示名、版本 ID、页码、片段、Evidence ID 和引用文字；问答包含已验证 Citation。工具成功返回 `ok: true`，失败返回稳定的错误对象，不返回 Python traceback：

```json
{
  "ok": false,
  "error": {
    "code": "index_not_ready",
    "message": "当前案件索引未就绪，请先建立或更新索引。"
  }
}
```

示例调用：

```json
{"name":"search_evidence","arguments":{"case_id":"<case-id>","query":"450000","limit":5}}
```

## 文件与安全边界

`CASEFILE_MCP_IMPORT_ROOTS` 是导入允许目录列表，多个目录按系统路径分隔符分开（Windows 为分号）。未设置、无效或目录不存在时，MCP 导入默认禁用。客户端传入的路径必须是绝对本地文件路径；拒绝 `..`、UNC 网络路径、目录、允许目录外的文件、符号链接或 junction 指向允许目录外的文件，以及非 PDF 文件。导入前检查 50 MiB 大小上限；PDF 内容和页数仍由原有导入服务校验。允许目录应只存放准备导入的 PDF，不应包含数据库、日志、配置或凭据。

`export_case_analysis` 返回内容，不接受输出路径。服务不提供删除、备份恢复、SQL、任意文件读取、shell 或凭据工具。`get_system_status` 只返回明确列出的布尔状态、版本和可选索引状态；不会包含数据库、配置或日志路径、API key 或服务端点。错误响应也不包含原始异常文本。

## 30–40 秒教师演示

1. 在 MCP Client 中让 Agent“列出案件”，观察 `list_cases`。
2. 让 Agent“在演示案件中搜索 450000”，观察 `search_evidence` 的文件、版本与页码。
3. 让 Agent“根据证据回答付款金额”，观察 `ask_case` 的引用和验证状态。
4. 让 Agent“给我案件时间线”，观察 `get_timeline`。演示前需准备已建立索引、已保存日期 Claim 的合成案件，并配置且启用问答模型。

## 已知限制

- 只支持本机 stdio；没有 HTTP/SSE、远程认证或多租户能力。
- MCP 不公开 `get_document_page`，避免不带案件归属的页面读取。
- 导入允许目录限制的是可读文件来源，不控制 MCP Client 如何使用收到的分析内容。
- 文件系统允许目录校验依赖规范路径解析；同机有权修改允许目录的恶意进程可造成检查与打开之间的竞态。应限制该目录的写入权限。
- 大型分析导出直接返回给客户端，客户端可能有消息大小限制。
