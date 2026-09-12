# ews

[中文](README.md) | [English](README.en.md)

> 面向 AI agent 的 Exchange Web Services CLI：稳定的 JSON 契约、本地只读缓存、受限写入。

这是一个把 agent 工具接到本地 Exchange 邮箱上的适配层。它只做一件事：把「读邮件、理解上下文、回复」变成一组**结果可预测、失败可判定**的命令，让 agent 无需理解 EWS 的 SOAP/XML 细节，也不会意外改动邮箱状态。

- **机器优先的输出**：除 `--help`/`--version` 外，每次调用只在 stdout 输出一个 JSON 对象；诊断信息一律走 stderr。
- **本地优先的读取**：`sync` 把邮件同步进本地 SQLite 缓存，读命令只读缓存，因此不受网络抖动影响。
- **受限且显式的写入**：只提供发送、保存草稿、回复、标记已读、移动和附件下载；**没有邮件或文件夹删除能力**。
- **凭据安全**：密码只存 macOS Keychain，绝不进入配置文件、命令行参数、输出或日志。

## 能力

| 类别 | 命令 |
| --- | --- |
| 配置与认证 | `set`、`config list\|show\|delete\|path`、`auth set-password\|status\|delete-password` |
| 连通性诊断 | `test`、`doctor` |
| 同步 | `sync`（增量，可续跑） |
| 读取 | `folder list`、`message list`、`message get`、`message thread`、`contact list`、`contact get` |
| 目录 | `contact search`（实时查询企业通讯录 GAL，不缓存） |
| 写入 | `message send`、`message reply`、`message reply-all`、`message draft create\|reply\|reply-all`、`message mark-read`、`message move` |
| 附件 | `attachment save`（仅下载，流式，拒绝覆盖） |

## 环境要求

- **macOS**：依赖系统 Keychain 与系统信任库（Truststore）做 TLS 校验。
- **Python ≥ 3.14** 与 **uv**。
- 企业网络内可达的 EWS endpoint（HTTPS + NTLM），且使用显式 endpoint（不使用 Autodiscover）。
- 已在一个 **Microsoft Exchange Server 2019** 邮箱上完成完整验收；其他服务器版本尚未声明兼容性。

## 安装

作为工具安装（推荐日常使用）：

```nu
uv tool install git+https://github.com/Ccccraz/ews
```

从源码安装（开发或想改代码）：

```nu
git clone https://github.com/Ccccraz/ews
cd ews
uv sync
uv run ews --help
```

## 快速开始

```nu
# 1. 交互式写入非敏感配置；密码无回显地存入 macOS Keychain
ews set

# 2. 验证配置、Keychain、系统 TLS 与 NTLM 登录，并返回服务器版本
ews --user agent test

# 3. 首次同步（全量，数千封邮件约 1–2 分钟）；之后每次都是增量
ews --user agent sync

# 4. 看文件夹树：folder ID 和 well-known name 都能用于后续选择文件夹
ews --user agent folder list

# 5. 读邮件：结构化过滤 + 分页
ews --user agent message list --read-state unread --limit 20
ews --user agent message get <message-id>

# 6. 读整串会话（跨文件夹、按时间正序、默认含正文），用来理解上下文
ews --user agent message thread <message-id>

# 7. 回复：正文只写你要新增的内容，引用块由服务器生成
"Thanks, will follow up tomorrow." | ews --user agent message reply-all <message-id> --body-file -

# 8. 保存回复草稿供人工检查，不发送邮件
"Draft response" | ews --user agent message draft reply <message-id> --body-file -
```

`--user` 是全局选项，接受 NTLM 用户名或邮箱地址（大小写不敏感）。

## 命令一览

| 命令 | 用途 | 需要 `--user` | 依赖缓存已就绪 |
| --- | --- | --- | --- |
| `set` | 交互式新增或按 mailbox 更新 profile | 否 | 否 |
| `test` | 一次真实 Inbox 元数据请求 | 是 | 否 |
| `doctor` | 校验配置、Keychain、系统 TLS、NTLM | 是 | 否 |
| `sync [--progress]` | 增量同步进本地缓存（邮件 + 个人联系人） | 是 | 否（写缓存） |
| `folder list` | 文件夹树 + folder ID + well-known name | 是 | 是 |
| `contact list` | 联系人列表（文件夹/文本过滤 + 分页） | 是 | 是 |
| `contact get <id>` | 单条联系人常用字段 | 是 | 是 |
| `contact search <query>` | 实时搜索企业通讯录 GAL | 是 | 否（联网） |
| `message list` | 结构化过滤 + 分页 | 是 | 是 |
| `message get <id>` | 单封详情（正文、headers、附件元数据） | 是 | 是 |
| `message thread <id>` | 整串会话 | 是 | 是 |
| `message send` | 发送新邮件 | 是 | 否 |
| `message reply` / `reply-all <id>` | 回复 | 是 | 是 |
| `message draft create` | 保存新邮件草稿 | 是 | 否 |
| `message draft reply` / `reply-all <id>` | 保存回复草稿 | 是 | 是 |
| `message mark-read <id> [--unread]` | 标记已读 / 未读 | 是 | 是 |
| `message move <id> --folder <id\|name>` | 移动到其他文件夹 | 是 | 是 |
| `attachment save <mid> <aid> --path <file>` | 流式保存附件 | 是 | 是 |
| `config list` / `path` | 列出全部非敏感 profile / 查看配置路径 | 否 | 否 |
| `config show` / `delete` | 查看 / 删除所选 profile | 是 | 否 |
| `auth set-password` / `status` / `delete-password` | 管理所选 profile 的 Keychain 密码 | 是 | 否 |

`message list` 的过滤器可任意组合（AND）：`--folder`、`--read-state read|unread|any`、`--sender`、`--subject-contains`、`--body-contains`、`--received-from`、`--received-before`，分页用 `--limit`（默认 50、最大 200）与 `--offset`。`message thread` 支持 `--limit`（默认 20、最大 200）与 `--offset`。

`contact list` 支持 `--folder`（联系人文件夹 ID 或 `contacts`）、`--search`（匹配显示名、公司、部门与邮箱，大小写不敏感）与同样的 `--limit`/`--offset`；默认返回全部联系人文件夹并按 `file_as` 排序。`contact get <id>` 返回单条联系人的常用字段（姓名、公司、部门、职位、邮箱/电话/地址/IM、备注、生日等）。

`contact search <query>` 是**唯一联网的读取命令**，用于实时查询企业通讯录（GAL / OWA People → Directory，对应 EWS `ResolveNames`）：按姓名片段、alias 或邮箱前缀匹配，返回显示名、主 SMTP 地址、`mailbox_type`（`Mailbox` / `PublicDL` / `PrivateDL` 等）、名/姓、公司、部门、职位、带 label 的邮箱/电话/地址。它不读也不写本地缓存、不需要先 `sync`；`--limit` 默认 25、最大 100，服务端单次上限 100，触顶时返回 `truncated: true`，应细化查询。典型用法：先 `contact search` 拿到邮箱地址，再用于发信 / 回复。

写命令的正文通过 `--body-file <path>` 提供（`-` 表示 stdin），并用 `--content-type text|html`（默认 `text`）指定正文类型。`message draft create` 允许暂不提供任何收件人。

## 输出契约

成功与失败共用同一个版本化 envelope，stdout 始终只有一个 JSON 对象：

```json
{"schema_version":1,"ok":true,"data":{"user":"agent@example.com","folders":[{"id":"AAMk…","parent_id":null,"name":"Inbox","well_known_name":"inbox","total_count":1969,"unread_count":23}]}}
```

```json
{"schema_version":1,"ok":false,"error":{"code":"cache_not_ready","message":"Mailbox cache is not ready; run ews --user agent@example.com sync","details":{},"retryable":false}}
```

退出码：

| 退出码 | 含义 | 典型 `code` |
| --- | --- | --- |
| `0` | 成功 | — |
| `1` | 未预期的内部错误（stdout 仍是 envelope，traceback 只写 stderr） | `internal_error` |
| `2` | CLI 参数或配置错误 | `invalid_argument`、`destination_exists`、`configuration_error`、`cache_error` |
| `3` | 认证错误 | `authentication_error` |
| `4` | 目标资源不存在或缓存未就绪 | `resource_not_found`、`cache_not_ready`、`profile_not_found` |
| `5` | 网络、TLS 或 EWS 服务错误（`retryable: true`，可重试） | `service_error` |

诊断与日志：

- 诊断只写 **stderr**，默认每行一个 JSON 对象；`--log-format console` 切换为人类可读（stderr 是终端且未设置 `NO_COLOR` 时着色）。
- `--log-level error|warning|info|debug`（默认 `warning`）调整详细程度；`debug` 会打印 exchangelib 的请求级细节。
- 唯一例外是 `sync --progress`：进度条直接写 stdout，仅在该开关下出现。
- 日志与 traceback 都不包含密码（应用内密码值是 `SecretStr`，traceback 不打印局部变量）。

## 配置与凭据

非敏感配置为 TOML，固定在 `$HOME/.config/taskseed/ews/profiles.toml`。每个
`[[profiles]]` 保存一个 profile；所有 mailbox 与 NTLM username 作为别名，在忽略大小写后必须唯一：

```toml
[[profiles]]
[profiles.server]
endpoint = "https://webmail.example.com/EWS/Exchange.asmx"

[profiles.user]
mailbox = "agent@example.com"
username = "agent"

[[profiles]]
[profiles.server]
endpoint = "https://webmail.example.com/EWS/Exchange.asmx"

[profiles.user]
mailbox = "operator@example.com"
username = "operator"
```

密码单独存放于 macOS Keychain：service 为 `taskseed.ews:<endpoint-host>`，account 为 NTLM username。密码**不得**出现在 TOML、命令行参数、stdout、stderr 或日志中；`config show` 永不显示秘密。`config delete` 先删除所选 Keychain 密码，再删除 profile；密码本来不存在也成功，Keychain 后端失败则保留 profile。SQLite 邮箱缓存始终保留。

旧版 `$HOME/.config/taskseed/ews/profile.toml` 不会被读取或自动迁移。升级时，可为每个账户重新运行 `ews set`；也可手工创建上述 `profiles.toml`，把原 `[server]`、`[user]` 分别改为 `[profiles.server]`、`[profiles.user]` 并在前面加入 `[[profiles]]`。Keychain 键格式没有变化；除非 endpoint host 或 NTLM username 也发生变化，否则无需重新保存密码。

## 本地缓存与同步

- 缓存是 SQLite 文件 `$HOME/.config/taskseed/ews/cache.db`，只存邮件元数据与正文以及个人联系人的常用字段，不存附件内容与联系人照片。
- 读取类命令**只读缓存**；缓存在完成一次完整 `sync` 之前不可读，此时返回 `cache_not_ready`/4 并提示先 `sync`。受此门控的包括 `contact list|get`。`contact search` 是例外：它实时查企业通讯录，不读也不写缓存。
- `sync` 同时同步邮件文件夹与个人联系人文件夹（`IPF.Contact`），写入同一份缓存并共用一个 `ready` 标记。
- 写入类命令**只改远端、不改缓存**：`mark-read` 与 `move` 的效果由下一次 `sync` 收敛，所以「写后立即读」可能看到写前快照。
- `sync` 是增量的、按文件夹逐个推进，并且**可续跑**：中途失败只留下一致的前缀状态，下一次从断点继续；服务端 sync state 过期时会自动重新全量枚举该范围，对使用者透明。
- 缓存代表「最近一次已同步的视图」，不是某一时刻的一致快照；schema 升级会强制重建缓存，需要重新 `sync`。

## 给 agent 的使用约定

- **stdout 只解析 JSON**，不要假设会有多行输出；诊断与进度类信息只出现在 stderr（`sync --progress` 除外）。
- **按 `code` 与 `retryable` 决策**：`service_error` 可重试；`cache_not_ready` 应先 `sync`；`resource_not_found` 说明本地缓存里没有这条数据，同样先 `sync` 再看。
- **写命令不做交互确认**：调用显式写命令即表示授权执行；需要用户同意时应在调用前确认。
- **草稿命令永不发送**：`message draft create|reply|reply-all` 只保存到 Exchange Drafts；需要执行一次 `sync` 后才能通过本地读命令看到新草稿。
- **没有删除能力**：CLI 不提供任何删除邮件或文件夹的命令，测试或误操作产生的邮件需要人工清理。
- **附件只下载不覆盖**：`attachment save` 遇到已存在的目标文件返回 `destination_exists`/2，且没有 `--overwrite`；`kind="item"` 的内嵌邮件/日历附件返回 `invalid_argument`。
- **引用块由服务器生成**：`message reply` / `reply-all` 只需提供你要新增的正文。

## 已知限制

- 仅 macOS；支持多个独立邮箱 profile，但不支持共享邮箱与 impersonation，也没有默认或当前 profile。
- 不支持 Autodiscover、自定义 CA、跳过 TLS 校验。
- 首版不含：邮件删除、转发、修改/发送/删除已有草稿、草稿或发送附件、日历与任务、MIME `.eml` 导出。
- 通讯录：个人联系人（`IPF.Contact`）只读并有本地缓存；企业通讯录（GAL）通过 `contact search` 实时查询，**不做全量下载/导出**（EWS 不允许浏览 GAL）。不含联系人写命令、联系人分发列表（`IPF.Contact.DistributionList`，但 GAL 搜索会返回分发列表条目）与联系人照片。
- `folder list` 对没有 EWS distinguished name 的文件夹返回 `well_known_name: null`（例如自定义文件夹、主邮箱里名为 `Archive` 的文件夹），这些文件夹只能用 folder ID 选择。

## 开发

```nu
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest          # 启用了分支覆盖率，门槛 90%
```

- 类型检查使用 Pyright **strict**；只有 exchangelib 所在的适配器模块局部关闭第三方缺失类型诊断。
- 测试使用 fake gateway 隔离网络与 Keychain，因此可以在普通 CI 中运行。
- 真实 EWS 验收无法在 CI 中运行：需要在企业网络内用真实邮箱手工执行（`doctor` 记录服务器版本，然后覆盖同步、分页、过滤、发送与回读、三类草稿保存、mark-read/reply/move、附件保存）。

## License

MIT © 2026 HuYang
