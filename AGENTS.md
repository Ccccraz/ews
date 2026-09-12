# AGENTS.md

## 1. 项目技术约束

- Python 版本为 `>=3.14`，使用 uv 管理项目依赖并提交 `uv.lock`。
- Pyright 必须使用 strict 模式并全量通过。
- 数据模型、配置、请求、响应和错误模型统一使用 Pydantic v2。
- CLI 使用 Cyclopts；公开命令的机器输出遵循稳定的 JSON 契约。
- EWS 集成使用同步 `exchangelib 5.6.x`，不得自行实现 SOAP 客户端。
- `exchangelib` 只能存在于基础设施适配层；其无类型对象不得泄漏到应用层或 CLI 层。
- EWS 适配器必须实现项目自有的严格类型 `MailboxGateway` Protocol，并在边界处转换成 Pydantic 模型。
- 非敏感配置使用 TOML，并统一存放在 `$HOME/.config/taskseed/ews/`（`$HOME` 即 Python `Path.home()`）；密码只存储在系统 keyring 中。
- 业务结果写 stdout；诊断日志写 stderr。密码不得进入配置文件、命令行参数、输出或日志。
- 日志使用 `structlog`：默认把诊断渲染为 stderr 上的 JSON 行，`--log-format console` 是显式的人类可读模式；第三方标准库 `logging` 走同一个 stderr handler。
- 使用显式构造参数进行依赖注入，不引入依赖注入框架。
- 使用 Ruff 做格式化和 lint，使用 pytest 与 pytest-cov 做测试和覆盖率检查。
- 不引入异步框架，除非未来有经过验证的并发需求并新增 `docs/adr/` 下的决策记录。

完整技术实现（分层边界、CLI 契约、缓存与同步机制、EWS 字段映射、兼容性基线）见
[`docs/architecture.md`](docs/architecture.md)。

## 2. 项目环境约定

- 项目已有的 `.venv` 是正常开发环境，可以由 `uv run` 直接使用，不应作为临时产物清理。
