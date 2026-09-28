# 第 10 阶段任务 6：最终交付验收与 v1.0.0 封版

> 历史验收记录：下文反映当时的私有封版状态。本公开候选已新增 AGPL LICENSE，尚未发布原 v1.0.0 Windows ZIP；第三方 notices 仍在核对。

本轮限定为发布验收与最小修复，没有扩展分析功能、修改 citation 数据模型或放宽 QA / Claim 验证。

## 基线与最小修复

起始 HEAD 为 `4bd8cfe8bec798ad3faa5e249513830c9f2ce9ad`，工作区干净。基线重新运行 pytest：320 passed；Node：25 passed；Ruff 和 strict Mypy 通过。

GUI 修复 commit：`f613d2c0943a13b45e2ca348158762d41fcdcf48`。

1. 历史引用可能误认成当前材料：通过既有 document API 展示真实 v1 / v2 与非当前标记，原文链接仍绑定原 version ID。无 metadata 时提示版本信息暂不可用。
2. 恢复后的配置提示必须与产品一致：改为实际 `config.env` 路径。
3. 路径展示不是单独的 blocker，但可低风险改善：系统状态折叠技术详情显示白名单 data / database / logs / configuration 路径，并转义 HTML；不展示凭据。

## RC 构建与实际 ZIP 安装

- RC 文件：`CaseFileAI-1.0.0-rc-windows.zip`，内部版本仍是历史 `0.1.0`，验收后才升级。
- RC 构建来源：上述 GUI 修复 commit。
- RC ZIP 构建时间（UTC）：2026-09-27T07:52:02.535618。
- RC ZIP SHA256：`06c0a4ae4b31dbe3fd250658e1080caa0f046d3fd86878f8a08ff6292cb98a19`。
- RC wheel SHA256：`227ddfcfad040afff9960087c7b271a9367abcd4fdbc0677f242428e395a9a9e`。
- Python 3.12.13，uv 0.11.21，与 Windows runtime 锁定文件一致。

验收从该 ZIP 解压开始，使用新的临时程序目录、LocalAppData、配置、runtime 和 venv；目录含中文与空格，cwd 为非仓库目录。RC 使用新的 uv 依赖缓存，但复用了本机 Python 安装与 Hugging Face 向量模型缓存（OCR 使用新环境内的模型文件）。不是独立物理干净 Windows，也不是完全冷机验证。

实际执行 Setup、Prepare Models、Start、Stop 与重启；自动浏览器打开调用成功，真实 socket 是 `127.0.0.1:8000`。本地关闭云模型时，原生 PDF、真实扫描 OCR、索引与检索、全部原文 SHA、案件重命名及归档恢复、JSON / Markdown 导出通过。最终 1.0.0 ZIP 重新安装，并在无云模式创建完整本地备份。

## 固定真实 Qwen 与证据链

使用 `qwen3.7-plus`，合成材料，9 个固定问题各提交一次，QA 重提交数 0。

| 项目 | 实际结果 |
| --- | --- |
| 生椰拿铁普通问题 | validated，同名合成 PDF 第 2 页 |
| 马斯克无证据问题 | 安全拒答 |
| 价格字段偷换 | 安全拒答 |
| 指定第 1 页问配方 | 安全拒答，没有跨页验证 |
| 两事实回答 | validated，日期 / 金额各一条 Claim |
| 另一金额材料 | validated |
| 行为正向题 | claim_partially_supported，安全拒答 |
| 行为否定题 | validated |
| 指定第 2 页问配方 | validated，来源范围正确 |

false answer 0、false abstain 1、provider error 0。两事实 Claim 主体 / 对象范围、category、Evidence binding 与 Coverage 均通过，unsupported 0。

金额候选实际保留 `uncertain / amount_parse_incomplete`，没有错误确定结论。行为正向题拒答，因此没有案件内的行为 Claim 对。独立固定合成 fixture 调用 RC 的生产 service：金额规则返回 `contradiction / normalized_amount_mismatch`；一次真实 Qwen 行为语义调用返回 `contradiction / model_incompatible_same_slot`。fixture 只在内存中使用，未写入案件，不冒充真实问答产生的 Claim。

验收脚本最初错误地要求每个正向问题及金额候选都必须确定成功；按用户明确允许安全拒答和 uncertain 的规则修正了验收判定，保留原结果，没有修改产品验证。另修正脚本写入 dotenv 时对中文路径使用 JSON Unicode 转义的错误，在同一恢复副本继续验收，没有重复云问题。

## 历史版本、恢复与安全

合成合同 A 添加 v2 后，旧回答与 Claim 仍引用真实 v1；GUI 生产 JS 对真实 HTTP 的渲染检查显示 `v1（非当前版本）`，原文 v1 SHA 正确。JSON 保留 version，Markdown 中文、文件名、页码、原文、待确认与已知限制可读；拒答历史不会冒充 validated 结果。

RC 完整备份恢复至新目录，SQLite integrity_check 与 foreign_key_check 通过，PDF SHA 全部一致。恢复副本包含：案件 1、文档 6、版本 7、回答 9、Claim 4、AnswerCitation 6。副本独立启动后案件、历史回答 / Claim、搜索与 GUI HTTP 可读。最终 1.0.0 使用这份历史备份再次验证数据兼容与导出 metadata。

替换整个程序位置后 Setup / Start 保留原配置 hash 与案件 ID。重复三次 Start 复用实例、Stop / Restart、stale PID 恢复与端口冲突均经实际 ZIP 执行；PID reuse、防误停其他 Python、异常 parser、失效备份 / checksum 和 provider 错误使用既有确定性测试。

安全检查覆盖 Git tracked files、ZIP、wheel、日志、runtime、备份、导出和系统状态，检查实际凭据值而不是把代码中的 Authorization 字段名误认为泄露。运行期间 dedicated control token 文件是受控停止能力的预期存储；不会复制到其他交付文件，并在 Stop 后清理。备份 / 导出中的合成案件原文是预期内容。

## 最终交付与验收边界

最终 1.0.0 全套自动化：`uv run pytest -q` 为 320 passed（204.09 秒，1 条既有 Starlette / AnyIO 弃用警告）；`node --test tests/frontend/workspace.test.cjs` 为 29 passed、0 skipped；Ruff 通过，strict Mypy 通过（89 个源码文件）。全套 pytest 包含实际 wheel 新环境安装、迁移、CLI HTTP 与静态资源 smoke，以及全部异常路径回归。

最终文件为 `dist/CaseFileAI-1.0.0-windows.zip` 和对应 wheel；最终 ZIP 生成后再写入 `dist/SHA256SUMS.txt`。ZIP 包含四个入口、锁文件、运行所需源码 / migrations / web assets、wheel、配置模板、中文手册及发布说明，不包含 Git、tests、真实 .env、用户数据、日志或评测结果。项目没有既有 LICENSE / NOTICE 文件，不编造许可证。

最终构建来源、时间、文件 SHA、全套检查结果与实际最终 ZIP smoke 记录在 `dist/release-report.json`、`dist/final-acceptance.json`；避免把 ZIP 自身校验值写回 ZIP 内造成循环构建。

浏览器自动打开已执行；本轮 UI 的自动验证限于真实 HTTP 与 Node 行为测试，未完成新的浏览器视觉验收。需要用户最终手动确认：旧引用版本标记与原 PDF、恢复提示的 config.env、系统状态折叠技术详情的真实路径。此前人工验收不替代这次新增 UI 的手动确认。

已知限制与产品定位见 [发布说明](../RELEASE_NOTES.md) 和 [用户手册](user-guide-zh.md)。本任务不开始第 11 阶段。
