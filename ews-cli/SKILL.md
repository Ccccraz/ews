---
name: ews-cli
description: 通过 ews-cli 操作本地 Exchange 邮箱（EWS）。当任务涉及读取或检索邮件/会话/文件夹/联系人、搜索企业通讯录、保存回复草稿、标记已读或移动邮件时使用。写操作仅限草稿系列，绝不发送真实邮件；不支持删除。
---

# ews-cli

`ews-cli` 是把本地 Exchange 邮箱接到 agent 的 CLI。读操作只读本地 SQLite 缓存，写操作受限且显式；除 `--help`/`--version` 外，每次调用在 stdout 只输出一个 JSON 对象，诊断信息一律走 stderr。

## 何时使用

- 读取或检索邮件、整串会话、文件夹树、个人联系人。
- 实时查询企业通讯录（GAL）。
- 为邮件撰写回复并保存为草稿，供人工在 Drafts 中检查后发送。
- 标记已读 / 未读，或把邮件移动到其他文件夹。

## 核心约定

- 所有命令都带全局选项 `--user <mailbox 或 username>`（忽略大小写），用于选择 profile；没有默认 profile。
- **stdout 只有单个 JSON envelope**：`{"schema_version":1,"ok":true,"data":{...}}` 或 `{"schema_version":1,"ok":false,"error":{"code","message","details","retryable"}}`。不要假设多行输出。
- 退出码：`0` 成功；`1` 内部错误；`2` 参数/配置错误；`3` 认证错误；`4` 资源不存在或缓存未就绪；`5` 网络/TLS/服务错误（`retryable:true`）。
- **按 `code` 与 `retryable` 决策**：`service_error` 可重试；`cache_not_ready` 先 `sync`；`resource_not_found` 先 `sync` 再看；`profile_not_found`/`authentication_error` 让用户先执行 `ews-cli set`。

## 标准工作流

```nu
# 1. 读取前先同步（增量、可续跑；同步同时拉取邮件与个人联系人）
ews-cli --user <u> sync

# 2. 列出并读取
ews-cli --user <u> folder list
ews-cli --user <u> message list --folder inbox --read-state unread --limit 20
ews-cli --user <u> message get <message-id>
ews-cli --user <u> message thread <message-id>

# 3. 回复：只写你要新增的正文，引用块由服务器生成；保存为草稿
"Thanks, will follow up tomorrow." | ews-cli --user <u> message draft reply-all <message-id> --body-file -
```

- 会话（thread）跨全部缓存文件夹、按时间正序；`message thread <id>` 默认含正文。
- 写命令（下面允许的草稿/已读/移动）**只改远端、不改本地缓存**：写后立即读可能看到写前快照；需要再 `sync` 才能通过读命令看到结果。
- 正文通过 `--body-file <path>` 提供（`-` 表示 stdin），并用 `--content-type text|html`（默认 `text`）。

## 命令参考

| 目的 | 命令 |
| --- | --- |
| 同步（读前置） | `sync`（可选 `--progress`，仅此时 stdout 混入进度） |
| 文件夹 | `folder list` |
| 邮件读取 | `message list`、`message get <id>`、`message thread <id>` |
| 联系人 | `contact list`、`contact get <id>`、`contact search <query>` |
| 草稿（唯一允许的写入） | `message draft create`、`message draft reply <id>`、`message draft reply-all <id>` |
| 状态 | `message mark-read <id>`（`--unread` 取消已读）、`message move <id> --folder <id\|name>` |
| 附件 | `attachment save <mid> <aid> --path <file>` |

- `message list` 过滤器可任意 AND 组合：`--folder`、`--read-state read\|unread\|any`、`--sender`、`--subject-contains`、`--body-contains`、`--received-from`、`--received-before`；分页 `--limit`（默认 50、最大 200）与 `--offset`。
- `contact search <query>` 是唯一联网的读命令：实时查 GAL，不读也不写缓存、无需先 `sync`；`--limit` 默认 25、最大 100，触顶返回 `truncated: true`，此时细化查询。
- `attachment save` 遇到已存在目标文件返回 `destination_exists`/2，没有 `--overwrite`；只下载，不覆盖。

## 硬性约束（必须遵守）

- **只允许通过草稿系列写入**：`message draft create|reply|reply-all`。**禁止**调用 `message send`、`message reply`、`message reply-all` 真正发信——发信一律留给人工在 Drafts 中检查后完成。
- **没有删除能力**：CLI 不提供邮件或文件夹删除命令，不要尝试用其他方式删除或清理。测试产生的邮件由人工处理。
- 写命令不做交互确认：在允许范围内，显式调用即表示授权；但需要用户同意时应先确认。
- 凭据只存系统 keyring；不要尝试输出、记录或传递密码。配置由人工执行 `ews-cli set` 完成，agent 不代替用户输入密码。

## 前置条件与故障排查

- 初次配置由人工完成：`ews-cli set` 写入非敏感配置（endpoint、mailbox、username）并把密码存入系统 keyring，随后 `ews-cli --user <u> test`（或 `doctor`）验证配置、keyring、系统 TLS 与 NTLM 登录。
- 读命令（除 `contact search`）依赖缓存 ready；返回 `cache_not_ready`/4 时先 `sync`。
- 写入类命令不需要缓存：`sync`、`test`、`doctor`、`send`、`draft create`、`contact search` 与 `set`/`config`/`auth` 不依赖缓存。
