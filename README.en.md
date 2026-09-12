# ews

[中文](README.md) | [English](README.en.md)

> A CLI adapter between AI agents and Exchange Web Services: a stable JSON contract, a local read-only cache, and deliberately limited writes.

This project puts agent tooling on top of a local Exchange mailbox. It does one thing: turn "read mail, understand the thread, reply" into a set of commands with **predictable results and decidable failures**, so an agent never has to understand EWS SOAP/XML details and cannot accidentally change mailbox state.

- **Machine-first output**: apart from `--help`/`--version`, every invocation writes exactly one JSON object to stdout; diagnostics always go to stderr.
- **Local-first reads**: `sync` mirrors the mailbox into a local SQLite cache, and every read command only reads that cache, so reads do not depend on a stable network.
- **Explicit, limited writes**: send, reply, mark read, move and download attachments — and **no ability to delete messages or folders**.
- **Credential safety**: the password lives only in the macOS Keychain and never reaches configuration files, command-line arguments, output or logs.

## Capabilities

| Area | Commands |
| --- | --- |
| Configuration and authentication | `set`, `config list\|show\|delete\|path`, `auth set-password\|status\|delete-password` |
| Connectivity diagnostics | `test`, `doctor` |
| Synchronization | `sync` (incremental and resumable) |
| Reads | `folder list`, `message list`, `message get`, `message thread` |
| Writes | `message send`, `message reply`, `message reply-all`, `message mark-read`, `message move` |
| Attachments | `attachment save` (download only, streamed, never overwrites) |

## Requirements

- **macOS**: the system Keychain and the system trust store (via Truststore) provide TLS verification.
- **Python ≥ 3.14** and **uv**.
- Network access to an EWS endpoint inside the corporate network (HTTPS + NTLM), addressed by an explicit endpoint — Autodiscover is not used.
- Fully accepted against one **Microsoft Exchange Server 2019** mailbox; no other server version is claimed to be compatible yet.

## Installation

Install it as a tool (recommended for daily use):

```nu
uv tool install git+https://github.com/Ccccraz/ews
```

Or work from a source checkout (development, or to change the code):

```nu
git clone https://github.com/Ccccraz/ews
cd ews
uv sync
uv run ews --help
```

## Quick start

```nu
# 1. Interactively store the non-secret configuration; the password is read
#    without echo and written to the macOS Keychain.
ews set

# 2. Verify configuration, Keychain, system TLS and NTLM login, and report the
#    server version.
ews --user agent test

# 3. First synchronization (full; a few thousand messages take 1-2 minutes).
#    Every later run is incremental.
ews --user agent sync

# 4. Inspect the folder tree: both folder IDs and well-known names select a folder.
ews --user agent folder list

# 5. Read mail: structured filters plus pagination.
ews --user agent message list --read-state unread --limit 20
ews --user agent message get <message-id>

# 6. Read a whole conversation (across folders, oldest first, bodies included)
#    to understand the context.
ews --user agent message thread <message-id>

# 7. Reply: provide only the text you want to add, the server generates the quote.
"Thanks, will follow up tomorrow." | ews --user agent message reply-all <message-id> --body-file -
```

`--user` is a global option and accepts either the NTLM username or the mailbox address (case-insensitive).

## Command overview

| Command | Purpose | Needs `--user` | Needs a ready cache |
| --- | --- | --- | --- |
| `set` | Interactively add or update a profile by mailbox | no | no |
| `test` | One real Inbox metadata request | yes | no |
| `doctor` | Verify configuration, Keychain, system TLS, NTLM | yes | no |
| `sync [--progress]` | Incrementally synchronize into the local cache | yes | no (it writes the cache) |
| `folder list` | Folder tree with folder IDs and well-known names | yes | yes |
| `message list` | Structured filters plus pagination | yes | yes |
| `message get <id>` | One message: body, headers, attachment metadata | yes | yes |
| `message thread <id>` | One whole conversation | yes | yes |
| `message send` | Send a new message | yes | no |
| `message reply` / `reply-all <id>` | Reply to a message | yes | yes |
| `message mark-read <id> [--unread]` | Mark a message read or unread | yes | yes |
| `message move <id> --folder <id\|name>` | Move a message to another folder | yes | yes |
| `attachment save <mid> <aid> --path <file>` | Stream one attachment to disk | yes | yes |
| `config list` / `path` | List all non-secret profiles or show their path | no | no |
| `config show` / `delete` | Show or delete the selected profile | yes | no |
| `auth set-password` / `status` / `delete-password` | Manage the selected profile's Keychain password | yes | no |

The `message list` filters combine with AND: `--folder`, `--read-state read|unread|any`, `--sender`, `--subject-contains`, `--body-contains`, `--received-from`, `--received-before`. Pagination uses `--limit` (default 50, maximum 200) and `--offset`. `message thread` accepts `--limit` (default 20, maximum 200) and `--offset`.

Write commands take their body from `--body-file <path>` (`-` means stdin) and select the body type with `--content-type text|html` (default `text`).

## Output contract

Success and failure share one versioned envelope, and stdout always carries a single JSON object:

```json
{"schema_version":1,"ok":true,"data":{"user":"agent@example.com","folders":[{"id":"AAMk…","parent_id":null,"name":"Inbox","well_known_name":"inbox","total_count":1969,"unread_count":23}]}}
```

```json
{"schema_version":1,"ok":false,"error":{"code":"cache_not_ready","message":"Mailbox cache is not ready; run ews --user agent@example.com sync","details":{},"retryable":false}}
```

Exit codes:

| Exit code | Meaning | Typical `code` |
| --- | --- | --- |
| `0` | Success | — |
| `1` | Unexpected internal error (stdout still carries the envelope, the traceback goes to stderr) | `internal_error` |
| `2` | Invalid CLI arguments or configuration | `invalid_argument`, `destination_exists`, `configuration_error`, `cache_error` |
| `3` | Authentication error | `authentication_error` |
| `4` | Missing resource or cache not ready | `resource_not_found`, `cache_not_ready`, `profile_not_found` |
| `5` | Network, TLS or EWS service error (`retryable: true`) | `service_error` |

Diagnostics and logging:

- Diagnostics go to **stderr** only, one JSON object per line by default; `--log-format console` switches to human-readable output (coloured when stderr is a terminal and `NO_COLOR` is unset).
- `--log-level error|warning|info|debug` (default `warning`) controls verbosity; `debug` adds exchangelib's per-request detail.
- The single exception is `sync --progress`: the progress bar writes to stdout, and only when that flag is given.
- Logs and tracebacks never contain the password (in-app secrets are `SecretStr` values, and tracebacks do not print local variables).

## Configuration and credentials

The non-secret configuration is TOML, fixed at `$HOME/.config/taskseed/ews/profiles.toml`.
Each `[[profiles]]` entry is one profile. Mailboxes and NTLM usernames are aliases that
must be unique when compared case-insensitively:

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

The password is stored separately in the macOS Keychain: service `taskseed.ews:<endpoint-host>`, account = NTLM username. The password must **never** appear in the TOML file, command-line arguments, stdout, stderr or logs, and `config show` never reveals secrets. `config delete` deletes the selected Keychain password before deleting the profile; an already absent password is successful, while a Keychain backend failure preserves the profile. SQLite mailbox caches are always retained.

The old `$HOME/.config/taskseed/ews/profile.toml` is neither read nor migrated automatically. To upgrade, run `ews set` again for every account, or create `profiles.toml` manually: add `[[profiles]]` and rename the old `[server]` and `[user]` tables to `[profiles.server]` and `[profiles.user]`. The Keychain key format is unchanged, so passwords need not be saved again unless the endpoint host or NTLM username also changes.

## Local cache and synchronization

- The cache is the SQLite file `$HOME/.config/taskseed/ews/cache.db`. It stores message metadata and bodies, never attachment content.
- Read commands **only read the cache**. Until one complete `sync` has finished the cache is unreadable, and reads answer `cache_not_ready`/4 with a hint to run `sync` first.
- Write commands **change the remote mailbox only, never the cache**: the effects of `mark-read` and `move` converge on the next `sync`, so reading immediately after a write can still show the pre-write snapshot.
- `sync` is incremental, walks folders one by one, and is **resumable**: a failure half-way leaves a consistent prefix that the next run continues from. When the server rejects an expired sync state, that scope is re-enumerated from scratch, transparently to the caller.
- The cache represents "the most recently synchronized view", not a point-in-time snapshot. A schema upgrade forces a rebuild, after which `sync` has to run again.

## Notes for agent callers

- **Parse JSON from stdout only** and do not assume line counts; diagnostics and progress appear on stderr (`sync --progress` being the exception).
- **Decide from `code` and `retryable`**: `service_error` may be retried; `cache_not_ready` means run `sync` first; `resource_not_found` means the local cache does not hold that item yet — run `sync` and look again.
- **Write commands never ask for confirmation**: invoking one is the authorization. Ask the user before calling them when consent is required.
- **There is no delete capability**: the CLI exposes no command that deletes a message or folder, so anything produced by tests or mistakes has to be cleaned up manually.
- **Attachments are download-only and never overwrite**: `attachment save` returns `destination_exists`/2 when the target file already exists and offers no `--overwrite`; embedded `kind="item"` attachments return `invalid_argument`.
- **The server generates the quote**: pass only your new text to `message reply` / `reply-all`.

## Known limitations

- macOS only; multiple independent mailbox profiles are supported, but shared mailboxes, impersonation, and a default/current profile are not.
- No Autodiscover, no custom CA files, no way to skip TLS verification.
- Not in this first version: deleting, forwarding, drafts, sending attachments, calendar and contacts, MIME `.eml` export.
- `folder list` returns `well_known_name: null` for folders without an EWS distinguished name (custom folders, or the main-mailbox folder literally named `Archive`); those can only be selected by folder ID.

## Development

```nu
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest          # branch coverage is enabled with a 90% floor
```

- Type checking runs Pyright in **strict** mode; only the exchangelib adapter module suppresses the missing third-party type information locally.
- Tests use a fake gateway, so they isolate the network and the Keychain and run in ordinary CI.
- Real EWS acceptance cannot run in CI: it has to be performed manually inside the corporate network with a real mailbox (`doctor` records the server version first, then covers synchronization, pagination, filters, send-and-read-back, mark-read/reply/move and attachment saving).

## License

MIT © 2026 HuYang
