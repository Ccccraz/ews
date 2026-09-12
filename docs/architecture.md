# EWS CLI 架构与实现

本文件描述**当前实现**：分层与类型边界、CLI 公开契约、本地缓存与同步机制、EWS 字段映射和工程质量门槛。它是维护本项目的开发者与 agent 的技术参考，不记录决策历史。

- 使用方式、安装与快速开始：[`README.md`](../README.md)
- 仓库内的 agent 工作约定：[`AGENTS.md`](../AGENTS.md)

## 运行时与依赖

- 使用 Python `>=3.14`；依赖与构建由 uv 管理，提交 `uv.lock` 锁定完整依赖图；开发依赖为 Pyright、Ruff、pytest 和 pytest-cov。
- 直接依赖使用兼容范围，具体版本由锁文件确定：
  - `pydantic>=2.12,<3`、`cyclopts>=4.22,<5`、`email-validator>=2.3,<3`
  - `exchangelib>=5.6,<6`、`requests>=2.32,<3`、`truststore>=0.10,<1`
  - `keyring>=25,<26`、`tomli-w>=1.2,<2`
  - `sqlmodel>=0.0.42,<0.1`、`rich>=14,<16`、`structlog>=26,<27`
- 日志使用 structlog：默认把诊断渲染为 stderr 上的 JSON 行，`--log-format console` 是显式的人类可读模式；structlog 自带 PEP 561 类型标注，可直接通过 Pyright strict。
- 通过 `structlog.stdlib.ProcessorFormatter` 与标准库 `logging` 共用同一个 stderr handler 和同一条处理器链，因此 exchangelib 等第三方记录无需任何拦截代码就被统一格式化。异常渲染不得包含局部变量：JSON 路径用 `format_exc_info` 自行格式化回溯，console 路径固定 `plain_traceback`——项目已依赖 Rich，而 structlog 为已安装的 Rich 自动选择的回溯格式化器会打印每一帧的局部变量。

## 分层与类型边界

同步、单进程分层：

```text
CLI -> Application Service -> MailboxGateway Protocol -> Exchangelib Adapter
```

- CLI 层只负责参数解析、调用应用服务、序列化结果和选择退出码。
- 应用服务负责用例编排，不依赖 Cyclopts 或 exchangelib。
- `MailboxGateway` 是项目自有的最小邮箱能力 Protocol；Exchangelib Adapter 是唯一允许导入 exchangelib 的生产代码边界，第三方对象在边界处立即转换为项目 Pydantic 模型。
- `exchangelib 5.6` 不提供 `py.typed` 且注解不全，只在该适配器模块局部关闭必要的第三方类型诊断，其他代码继续完整 Pyright strict。
- 依赖通过构造参数注入，不引入依赖注入框架；不引入异步框架（CLI 每次只处理一个有限用例，exchangelib 本身同步）。

## 连接、凭据与配置

- 使用 exchangelib，不自行维护 EWS SOAP、WSDL 或 XML 映射；目标是本地 Exchange，使用 NTLM 与 HTTPS（[Microsoft 的认证说明](https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/authentication-and-ews-in-exchange)）。
- 使用显式 EWS endpoint 并关闭 Autodiscover；每个 profile 只对应一个邮箱，使用 delegate access，不支持共享邮箱或 impersonation。
- TLS 必须启用证书校验，并通过 Truststore 使用 macOS 系统信任库；不支持自定义 CA 或跳过校验。
- 非敏感配置（endpoint、mailbox SMTP address、NTLM username）使用 TOML 存放在 `$HOME/.config/taskseed/ews/profiles.toml` 的 `[[profiles]]` 数组中，标准库 `tomllib` 读取、Tomli-W 写入，并通过同目录临时文件原子替换。mailbox、username 及交叉别名忽略大小写后唯一；mailbox 是新增或更新时的稳定身份。
- 密码只存 macOS Keychain（service 为 `taskseed.ews:<endpoint-host>`，account 为 NTLM username），应用内使用 Pydantic `SecretStr`；密码不得出现在 TOML、命令行参数、stdout、stderr 或日志中。
- 旧 `profile.toml` 不读取也不自动迁移。升级时对每个账户重新执行 `ews set`，或手工把旧 `[server]`/`[user]` 包装为 `profiles.toml` 中的 `[[profiles]]`、`[profiles.server]`/`[profiles.user]`。Keychain 键格式不变。
- 不支持无桌面 Linux、环境变量密码回退和文件型 Keyring。

## 本地缓存

- 远端是唯一事实来源：缓存只存元数据与正文，不存附件内容；读取命令一律只读缓存，未就绪时返回 `cache_not_ready`/4 并提示先 `sync`。
- `ready` 只在一次完整 `sync` 的结尾写入。缓存文件不存在、schema 处于待重建版本、或该 mailbox 的 ready 状态缺失或为 false 时，读取一律返回 `cache_not_ready`/4。受此门控的是 `folder list`、`message list|get|thread`、`message reply|reply-all|draft reply|draft reply-all|mark-read|move` 和 `attachment save`；`sync`、`test`、`doctor`、`send`、`draft create` 与 `set`/`config`/`auth` 不依赖缓存。
- 写命令只修改远端、不修改本地缓存：mark-read 与 move 的效果由下一次 `sync` 通过 `READ_FLAG_CHANGE` 与 delete+create 收敛，因此"写后立即读"可能看到写前快照。
- schema 变更时强制重建（删除受影响的表并清空 item sync state 与 mailbox ready 标记，下一次 `sync` 全量重取），不做 `ALTER TABLE` + 回填：回填需要新增公开命令与额外代码，而全量重取本来就要重新走一遍 GetItem。
- 会话字段需要 `(mailbox, conversation_id)` 索引，v2 → v3 即按上述规则重建。

## 同步工作流与恢复

- 一次 `sync` 的顺序是：`initialize()`（建表，或按上面的规则重建旧 schema 且不置 ready）→ 层级同步 → 逐文件夹条目同步 → 全部成功后 `mark_ready`。
- 层级同步先读该 mailbox 的 hierarchy sync state，再调用 `SyncFolderHierarchy`；state 为 `None`（首次或刚重建）时是全量枚举。
- 条目同步按缓存中的文件夹顺序逐个执行：每个文件夹读自己的 item sync state，再调用 IdOnly `SyncFolderItems`。
- 只有 `CREATE`/`UPDATE` 的邮件需要再取详情，并按每批最多 10 封 `GetItem`（适配器硬上限）；`READ_FLAG_CHANGE` 直接按同步结果更新 `is_read`，不取详情。
- 每个 `apply_folder_changes` / `apply_message_changes` 是一个事务，事务内同时写入数据与所在作用域的 sync state；不存在覆盖整个 sync 的大事务。因此 sync 可续跑：中途失败留下的是"一致的前缀状态"，下一次 sync 从已持久化的状态继续。
- 缓存读取命令（`folder list`、`message list|get|thread`）只读缓存、从不触网；`sync` 是唯一写缓存的命令，`send` 与三类草稿命令都不写缓存；`attachment save` 仍从缓存校验消息与附件元数据，但内容要经 EWS 下载。
- 过期的 sync state 对用户透明自愈：适配器把 EWS 的 `ErrorInvalidSyncStateData` 翻成 `InvalidSyncStateError`，服务层按作用域捕获后从零重来（层级用 `sync_hierarchy(None)`，单个文件夹用 `sync_items(folder, None)`），并让该作用域以 `reset=True` 落地；用户既不会看到错误码，也不需要手动清缓存。
- `reset=True` 表示把该次 EWS 响应当作该作用域的完整事实：响应中不存在的文件夹行会被删除，并连带删除它的 item sync state 与缓存邮件；某个文件夹中不在响应里的邮件同样会被删除。
- 缓存是"最新已同步视图"，不是时间点一致的快照：sync 中途失败不会回退 `ready` 标记，此时各文件夹可能处于不同进度，下一次成功 sync 后收敛。这是刻意选择——失败留下的是可续跑的一致前缀，而回退 `ready` 会让一次网络抖动把原本可读的缓存变成不可读。

## CLI 契约

### 命令与全局选项

```text
ews set
ews config list|path
ews --user U config show|delete
ews --user U auth set-password|status|delete-password
ews --user U doctor|test
ews --user U sync [--progress]
ews --user U folder list
ews --user U message list [--folder F] [--read-state S] [--sender A] [--subject-contains T]
                            [--body-contains T] [--received-from D] [--received-before D]
                            [--limit N] [--offset N]
ews --user U message get    <message-id>
ews --user U message thread <message-id> [--limit N] [--offset N]
ews --user U message send      --to <addr>... [--cc <addr>...] [--bcc <addr>...]
                                 [--subject <text>] --body-file <path|-> [--content-type text|html]
ews --user U message reply     <message-id> --body-file <path|-> [--subject <text>]
                                 [--content-type text|html]
ews --user U message reply-all <message-id> --body-file <path|-> [--subject <text>]
                                 [--content-type text|html]
ews --user U message draft create [--to <addr>...] [--cc <addr>...] [--bcc <addr>...]
                                 [--subject <text>] --body-file <path|-> [--content-type text|html]
ews --user U message draft reply <message-id> --body-file <path|-> [--subject <text>]
                                 [--content-type text|html]
ews --user U message draft reply-all <message-id> --body-file <path|-> [--subject <text>]
                                 [--content-type text|html]
ews --user U message mark-read <message-id> [--unread]
ews --user U message move      <message-id> --folder <folder-id|well-known-name>
ews --user U attachment save   <message-id> <attachment-id> --path <file>
```

- `--user` 是全局选项，使用 NTLM username 或 mailbox 地址选择唯一 profile，匹配时忽略大小写。所有需要邮箱身份的命令都必须提供它；没有默认或当前 profile。
- `--log-level` 是全局选项，取值 `error`、`warning`、`info` 或 `debug`（大小写不敏感，默认 `warning`），只调整 stderr 诊断的详细程度；`--log-format` 取值 `json`（默认）或 `console`，决定诊断是结构化 JSON 行还是人类可读输出，`console` 只在 stderr 是终端且未设置 `NO_COLOR` 时着色。
- `--body-file` 只接受显式路径，`-` 表示 stdin（因此该参数启用 `allow_leading_hyphen`）。写命令不回显正文、不输出进度、不做交互确认：调用显式写命令即表示授权执行。
- 收件人在 CLI 层是 `list[str]`，`send` 的“至少一个收件人”由模型保证，`draft create` 则允许三组收件人都为空；已提供的地址都用 `EmailStr(check_deliverability=False)` 校验（不触发 DNS）。这类失败与其他参数错误一样是 JSON + 退出码 2。
- `--subject` 在 `send` 中默认为空串，在 `reply`/`reply-all` 中省略表示使用标准回复主题；`mark-read` 默认置为已读，`--unread` 置为未读。
- `set` 与 `auth set-password` 只接受无回显的交互输入（不接受密码参数），密码写入 Keychain；`config list` 返回按 mailbox 排序的全部 profile，`config show` 不显示秘密，`config path` 返回实际配置路径。`config delete` 先删除 Keychain 项再删除 profile，密码缺失视为成功，Keychain 后端失败时保留 profile，且永不删除 SQLite 缓存。`doctor` 做完整诊断并返回 Exchange build/version，`test` 只做一次真实 Inbox 元数据请求。

### JSON 输出与退出码

除 `--help` 和 `--version` 外，每次调用都在 stdout 输出且只输出一个版本化 envelope：`schema_version` 固定为 `1`，`ok` 为布尔值，成功含 `data`，失败含 `error{code, message, details, retryable}`。所有 JSON 使用 Pydantic 模型序列化，时间使用带时区的 ISO 8601，EWS ID 作为不透明字符串处理。

`sync --progress` 是该规则的显式人类模式例外：进度条直接写 stdout，且只在该开关下出现（Rich 因此是直接依赖）。

诊断日志只能写 stderr：只安装一个指向 stderr 的 handler，不得注册任何指向 stdout 的 handler。诊断格式属于诊断而非公开契约，不存在 `schema_version`。

退出码固定为 `0` 成功、`1` 未预期的内部错误、`2` CLI 参数或配置错误、`3` 认证错误、`4` 目标资源不存在、`5` 网络、TLS 或 EWS 服务错误。错误映射：

| 情况 | code | 退出码 | retryable |
| --- | --- | --- | --- |
| CLI 顶层捕获到的未预期异常（含构建上下文时的异常） | `internal_error` | 1 | false |
| 参数非法（收件人缺失或地址非法、正文文件不可读）、EWS 拒绝请求（非法收件人、超出大小限制）、附件目标路径不可用、`kind="item"` 附件 | `invalid_argument` | 2 | false |
| 附件目标文件已存在 | `destination_exists` | 2 | false |
| profiles TOML 损坏、身份冲突或本地 I/O 失败 | `configuration_error` | 2 | false |
| 缓存读写失败 | `cache_error` | 2 | false |
| Keychain 无密码或失败、NTLM 被拒、SendAs 被拒 | `authentication_error` | 3 | false |
| `--user` 选择不到 profile（含配置文件不存在或为空） | `profile_not_found` | 4 | false |
| 缓存未 ready | `cache_not_ready` | 4 | false |
| 消息、文件夹或附件不存在 | `resource_not_found` | 4 | false |
| 其他 EWS 或传输失败（含 change key 失效） | `service_error` | 5 | true |

change key 失效归入可重试的 `service_error`，因为重试会重新读取服务端状态。适配器用 `EwsNotFoundError` 与 `EwsRejectedError` 承载"不存在"与"请求非法"的区分；附件下载另加 `AttachmentNotFoundError`、`UnsupportedAttachmentError`、`DestinationExistsError` 和 `InvalidDestinationError`。

未预期的内部错误由 CLI 顶层捕获：stdout 仍只输出一个 envelope（`code` 为 `internal_error`、退出码 `1`、`retryable` 为 false，`details.type` 给出异常类名），完整 traceback 只作为诊断写入 stderr（默认级别即可见，且按上面的规则不含局部变量）。`SystemExit` 与 `KeyboardInterrupt` 不属于 `Exception`，不受此捕获影响。

## 读取行为

- `folder list` 从 `msgfolderroot` 深度遍历，排除 `PidTagAttributeHidden` 标记的隐藏文件夹，返回邮件导航中的邮件文件夹；`Sync Issues` 等同步诊断文件夹同样保留，并带各自的 distinguished name。
- `well_known_name` 只表示 EWS distinguished folder 身份：文件夹 ID 与某个 `DistinguishedFolderId` 解析出的 ID 相同时取该名字（`inbox`、`sentitems`、`drafts`、`deleteditems`、`junkemail`、`outbox`、`notes`、`conversationhistory`、`syncissues`、`conflicts`、`localfailures`、`serverfailures`），否则为 `null`。它不是兜底字段——`msgfolderroot` 只属于邮件文件夹根自身，自定义文件夹与主邮箱里那个名为 `Archive` 的文件夹都应为 `null`（EWS 的 `archiveroot`/`archivemsgfolderroot` 指在线存档邮箱，不能按名字套用）。每次 sync 都以该映射重写缓存中的名字，并补齐 EWS 因同步状态已越过而不再上报的 distinguished 文件夹。
- `message list` 默认读取 Inbox，按接收时间倒序；分页用 `offset + limit`（默认 50、最大 200，响应给出下一页 offset）；结构化过滤覆盖文件夹、已读状态、发件人、主题文本、正文文本和接收时间范围，多个条件按 AND 组合。
- `message get` 返回常用完整元数据、发件人、收件人、抄送/密送、时间、状态、Internet headers、原始正文及其 `text`/`html` 类型和附件元数据。"完整读取"不意味着暴露每个罕用 EWS 属性，也不包含 RFC 822/MIME `.eml` 导出。
- 文件夹选择器先识别任意 well-known name（`inbox`、`sentitems`、`drafts`、`deleteditems` 等），再回退到 EWS folder ID；读命令与 `message move` 共用。

## 会话（thread）读取

Exchange 的 conversation 就是这里所说的 thread：`ConversationId` 是会话标识（只在单个邮箱内有效，且只包含进入过本邮箱的邮件），`ConversationIndex` 编码层级与顺序（根 22 字节、每层回复追加 5 字节，因此回复的索引是父索引的前缀扩展），`ConversationTopic` 是最初的归一化主题。分组由服务器决定：带回复头的回复必然并入原会话（即使主题被改），同主题的新邮件偶尔也会被并入。

- `message thread <message-id>` 接受会话中任意一封的 id，是纯本地读取：只查缓存，不发起任何 EWS 请求。
- 排序为 `received_at` 升序、`id` 作为稳定次序键；`limit` 默认 20、范围 1–200，`offset ≥ 0`，`pagination` 复用 `message list` 的 `has_more`/`next_offset` 语义。范围是全部已缓存文件夹（含 Deleted Items），不提供文件夹过滤开关。
- `message list`/`get`/`thread` 共享的摘要字段包含 `conversation_id`、`conversation_topic`、`conversation_index`（字节的小写十六进制）、`conversation_depth`（由索引长度派生 `(len - 22) // 5`，下限 0；仅用于展示，不是 EWS 字段）、`is_draft`、`categories`、`flag_status`。
- `flag_status` 走扩展属性：exchangelib 5.6 没有 `flag` 字段，因此注册 `PidTagFlagStatus`（0x1090，Integer）并把 0/1/2 映射为 `none`/`complete`/`flagged`，未知值退化为 `none`。
- `message get` 另有 `text_body`（HTML 邮件的纯文本替代，已在兼容基线的 Exchange 2019 上实测）与 `references`；`message thread` 的条目在摘要之上再带 `folder_name`、`to`、`cc`、`attachments` 和 `body`（默认含正文，便于一次读懂整串）。Sent Items 中的副本没有传输头，`internet_headers` 为空数组（实测 Inbox 副本为 24 个 header），这是 EWS 的事实而不是缺陷。
- 失败模式：会话字段缺失（旧服务器、会议邀请等非 Message 条目）时退化为"只返回这一封"且 `conversation_id`/`conversation_topic` 为 `null`；`conversation_index` 缺失或短于 22 字节时 `conversation_depth` 为 `null`；消息所在文件夹已被删除时 `folder_name` 为空串；`offset` 超出范围时返回空数组与 `has_more: false`，`message_count` 始终是会话总数。

## 写入行为

- `message send` 用 `Message(...).send(save_copy=True)`，Sent 副本由 EWS 写入 Sent Items；`reply`/`reply-all` 用 exchangelib 的 `create_reply`/`create_reply_all` 加 `send(save_copy=True)`。
- `message draft create` 用 `Message(folder=account.drafts, ...).save()`；`draft reply`/`draft reply-all` 用 `create_reply`/`create_reply_all` 加 `save(account.drafts)`。草稿路径不调用 `send()`，并返回 SaveOnly 响应中的 message ID 与 change key。
- 省略 `--subject` 时由适配器计算标准回复主题：原主题为空则留空，已以 `RE:` 开头（忽略大小写）则保持原样，否则加 `RE: ` 前缀。该计算不依赖服务器默认行为，因此可确定性测试。
- `MailboxGateway` 为发送、保存新草稿、回复、保存回复草稿、设置已读状态和移动分别提供严格类型方法，`EwsClient` 是唯一实现。涉及已有邮件的写操作在适配器内先按 `ItemId(id=...)` 取一次服务端最新状态再变更（exchangelib 的 id 转换只接受 `ItemId` 或 `(id, changekey)` 元组，裸字符串不可用）。
- 读/同步路径的异常翻译 `_run` 与写路径的 `_run_write` 分开，两者共用 `_with_account` 连接构造。
- `send` 与 `draft create` 不接触缓存；`reply`/`reply-all`/`draft reply`/`draft reply-all`/`mark-read`/`move` 要求缓存 ready 且目标消息存在（与 `message get` 一致），`move` 的目标文件夹也从缓存解析。所有草稿写入只修改远端，下一次 `sync` 后本地读取才可见。
- 响应只报告 EWS 确认的事实，不虚构服务器未返回的 message ID：`send`/`reply` 只返回收件人与主题（SendAndSaveCopy 模式下 EWS 不返回 ItemId），`mark-read` 返回更新后的 change key（EWS 在 UpdateItem 响应中不为消息更新回传可用的 change key，因此该值由一次按 `is_read` 的 GetItem 读回，而不是取本地对象上的值），`move` 返回目标文件夹内的新 ID 与 change key。
- `mark-read` 与 `move` 每次只处理一封邮件，避免批处理中的部分成功语义。

## 附件下载

- 只支持下载：发送附件不在范围内；回复也无法携带附件，因为 EWS 的 `ReplyToItem` 合法属性里没有 `Attachments`（exchangelib 的 `BaseReplyItem` 同样没有该字段）。
- 用 `FileAttachment.fp`（`GetAttachment.stream_file_content`）分块流式写入，内存占用与附件大小无关；已在真实邮箱用 8.4 MB 附件验证流式与非流式 `content` 逐字节一致（sha256 相同）。
- 目标文件始终拒绝覆盖：`open(path, "xb")` 原子拒绝并返回 `destination_exists`/2，不提供 `--overwrite`；父目录不存在或路径是目录返回 `invalid_argument`/2。这两类检查在任何 EWS 请求之前完成，因此失败既不创建空文件也不触网。
- 只接受 `kind="file"` 附件（含 `is_inline=true` 的内嵌图片）；`kind="item"`（内嵌邮件/日历项）返回 `invalid_argument`，与 MIME `.eml` 导出的排除保持一致。
- 结果契约 `AttachmentSaveResult{user, message_id, attachment_id, name, content_type, path, bytes_written}`；附件内容不进入 stdout，agent 从磁盘读取。`bytes_written` 是实际落地字节数，真实服务器上 GetItem 的附件 `Size` 元数据可能大于 GetAttachment 的内容长度，两者不能当作相等契约。
- 与 `message get` 一致要求缓存 ready 且消息存在，并额外校验附件 id 与 `kind`；下载不修改本地缓存，复用 `run_write` 运行器，因此错误码与 `retryable` 语义与远端写命令一致。

## 测试与质量门槛

- Pyright 用 strict 模式检查 `src` 与测试代码；Ruff 负责格式化、导入排序和 lint；pytest-cov 启用 branch coverage，总覆盖率不得低于 90%。
- 测试分三层：单元测试覆盖 Pydantic 模型、TOML 配置、分页、过滤、错误映射、正文输入和附件路径冲突；CLI 契约测试用 fake gateway 验证每个命令的 JSON schema、退出码和 stdout/stderr 隔离；适配器测试用受控的第三方对象替身验证 EWS 字段映射、异常转换和附件流式保存。

## 兼容性与验收

- 真实 EWS 验收无法在普通 CI 中运行，必须在企业网络内用真实邮箱手工执行：先用 `doctor` 记录 Exchange build/version 并验证 TLS、Keychain 和 NTLM，再验证文件夹遍历、分页与全部过滤器，然后发送唯一主题邮件并验证 list/get/正文/Internet headers；分别保存新邮件、reply 与 reply-all 草稿，确认 Drafts 中存在且没有发信，再执行 `sync` 验证 `is_draft=true`；用预置带附件邮件验证元数据与文件保存，最后验证 mark-read、reply、reply-all 和 move。
- 验收中的测试数据一律由人工清理：CLI 不暴露删除能力，验收本身也只使用 `move` 复原，不执行任何删除。
- 兼容基线：`Microsoft Exchange Server 2019`（`Build=15.2.2562.46, API=Exchange2016`）于 2026-09-12 完整通过上述步骤；其他服务器版本在重复该验收前不做兼容性声明。

## 当前范围之外

- 邮件删除、转发，以及修改、发送或删除已有草稿；发送附件或草稿附件；以"新邮件"方式实现带附件的回复；附件上传。
- 日历和联系人；MIME `.eml` 导出（含内嵌邮件附件的导出）。
- 共享邮箱和 impersonation；默认/当前 profile；Autodiscover；OAuth、Kerberos/GSSAPI 和 Basic Auth。
- 自定义 CA、禁用 TLS 校验；无桌面 Linux 与 Windows 客户端。
- 长驻进程、JSONL 协议和异步执行。
- 显示名形式的收件人（`Name <addr>`）、回复时覆盖收件人、写后自动同步。
- `message list --conversation-id` 与跨文件夹列表（会话查询统一由 `message thread` 承担）、会话级写操作、树形输出；附件内容缓存或把附件内容放进 JSON。

## 变更约定

本文件与代码同步维护：改动命令面、JSON envelope、退出码、缓存 schema 或同步策略时，先更新本文件（必要时同时更新 `README.md` 的对外描述），再改实现。改变依赖选型、认证方式、目标平台或引入异步执行等结构性变更，先新增 `docs/adr/` 下的决策记录，再扩大实现范围。
