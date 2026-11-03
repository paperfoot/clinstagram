---
name: clinstagram
description: >
  Run Instagram accounts from the command line: post, reply to comments and DMs, manage the profile,
  read insights, find people. Multiple accounts, compact JSON for agents.
  Supports Meta Graph API (official, safe) and private API (full features).
  Three compliance modes: official-only, hybrid-safe, private-enabled.
metadata: {"openclaw": {"requires": {"bins": ["clinstagram"], "env": ["CLINSTAGRAM_CONFIG_DIR"]}, "primaryEnv": "CLINSTAGRAM_CONFIG_DIR", "emoji": "📸", "homepage": "https://github.com/paperfoot/clinstagram", "install": [{"pip": "clinstagram"}]}}
---

# clinstagram

Hybrid Instagram CLI for AI agents. Routes between Meta Graph API and instagrapi private API based on compliance policy.

## Install

```bash
pip install clinstagram
```

## Critical: Global Flags Before Subcommand

```bash
clinstagram --json --account main dm inbox     # CORRECT
clinstagram dm inbox --json                    # WRONG — Typer limitation
```

Global flags: `--json`, `--account NAME`, `--backend auto|graph_ig|graph_fb|private`, `--proxy URL`, `--dry-run`, `--enable-growth-actions`, `--full`, `--fields a,b.c`

Output is compact by default: CDN image/video URLs and null fields are left out (a missing key means null). Pass `--full` for everything, or `--fields id,code,caption` for just those keys.

Success envelopes go to stdout; error envelopes go to **stderr** (capture both).

## Accounts: check before you act

```bash
clinstagram --json account list
```

Each row: `name` (pass it as `--account NAME`), `username`, `backends`, `ig_user_id`, and `uses`, one line on which API the account goes through and what it can't do. Never guess an account name; the default account is the one with `is_default: true`.

- **Official API (graph_ig / graph_fb):** posts, reels, carousels, stories without link stickers, comments, DMs. Safe. Insights, hashtags and other accounts' profiles need the token to carry those Meta permissions; without them you get exit 7, which is final, not transient.
- **Private API (private):** everything else (story link stickers, activity, likes, follows, editing or deleting posts, profile edits). It can get an account restricted: pace writes, never change username, name, bio, picture and links in one go, and stop for a day after any 429, feedback_required or challenge.
- With both, the router uses the official API first and the private API only for what Graph can't do.

Connect the official API with a token from a Meta app (Instagram API use case, "unified login" configuration, the account added as an Instagram Tester):

```bash
clinstagram --account NAME auth connect-fb --token <EAA… token> --ig-user-id <Instagram user ID shown next to the account in the Meta app>
```

`--ig-user-id` is needed for these system-user tokens: their `/me` has no Instagram account.

## Quick Start

```bash
# Check status
clinstagram --json auth status

# Live-check configured credentials
clinstagram --json auth probe

# Set compliance mode
clinstagram config mode official-only    # Graph API only, zero risk
clinstagram config mode hybrid-safe      # Graph primary, private read-only (default)
clinstagram config mode private-enabled  # Full access, user accepts risk

# Connect backends
clinstagram auth connect-ig --token ...   # Store Instagram Login token
clinstagram auth connect-fb --token ... [--ig-user-id ID]  # Facebook Login or Meta system-user token
clinstagram auth login         # Private API (username/password/2FA via instagrapi)
```

## Commands

| Group | Commands | Notes |
|-------|----------|-------|
| `agent-info` | `agent-info`, `info` (alias) | Bare JSON manifest (ACF compliant) |
| `doctor` | `doctor [--deep] [--account NAME]` | Check environment and session health |
| `update` | `update [--check] [--yes] [--pre]` | Check for or perform clinstagram updates |
| `auth` | `status`, `login`, `connect-ig`, `connect-fb`, `probe`, `logout` | `status` is configured-only; `probe` is live validation |
| `post` | `photo`, `video`, `reel`, `carousel`, `edit MEDIA --caption`, `delete MEDIA` | Local write media requires `private-enabled` or a public URL |
| `dm` | `inbox`, `pending`, `approve ID`, `seen ID`, `thread ID\|@user`, `send @user "text"`, `send-media`, `search` | Cold DMs = private API only; numeric target = reply; `pending` = message requests |
| `story` | `list [@user]`, `post-photo`, `post-video`, `viewers ID`, `delete ID` | `--link URL` adds a link sticker, `--mention @x` a mention |
| `comments` | `list MEDIA`, `add`, `reply`, `delete`, `like`, `unlike`, `pin`, `unpin` | pass the `comment_ref` from `comments list`; like/unlike need `--enable-growth-actions` |
| `analytics` | `profile`, `post MEDIA\|latest`, `insights MEDIA`, `account`, `hashtag TAG` | `insights`/`account` need a Business or Creator account |
| `followers` | `list [--user @x]`, `following [--user @x]`, `follow @user`, `unfollow @user` | defaults to your account; follow/unfollow need `--enable-growth-actions` |
| `user` | `info @user`, `search QUERY [--limit N]`, `posts @user`, `tagged @user` | official search is username-style lookup, broad search is private |
| `hashtag` | `top TAG`, `recent TAG` | |
| `like` | `post MEDIA`, `undo MEDIA` | Needs `--enable-growth-actions` |
| `media` | `download MEDIA [--output DIR]`, `likers MEDIA` | Photos, reels, carousels |
| `profile` | `show`, `edit [--bio] [--name] [--username] [--website]`, `picture PATH\|URL\|--remove`, `convert --to creator\|business` | Your own account. Instagram ignores `--website` via API (exit 4); set links in the app |
| `account` | `list [--check]`, `use NAME`, `remove NAME --yes`, `set-proxy NAME URL\|none` | One login, device and proxy per account. Pick per call with `--account NAME` or `CLINSTAGRAM_ACCOUNT` |
| `activity` | `activity [--limit N]` | Notifications: follows, likes, comments, mentions, tags |
| `config` | `show`, `mode MODE`, `set KEY VAL` | Modes: `official-only`, `hybrid-safe`, `private-enabled` |

`MEDIA` can be a numeric media ID, a shortcode (`DfPQchkG1tg`) or a post/reel URL. On the Graph backends, pass the Graph media ID.

## JSON Output

Success:
```json
{"exit_code": 0, "data": {}, "backend_used": "graph_fb"}
```

Error:
```json
{"exit_code": 2, "error": "session_expired", "remediation": "Run: clinstagram auth login"}
```

## Exit Codes

| Code | Meaning | Action |
|------|---------|--------|
| 0 | Success | Parse `data` |
| 1 | Bad arguments, or the user/post doesn't exist | Fix the input |
| 2 | Auth error | Run `remediation` command |
| 3 | Rate limited | Wait `retry_after` seconds |
| 4 | API error | Retry |
| 5 | Challenge required | Check `challenge_type`, prompt user |
| 6 | Policy blocked | Change compliance mode |
| 7 | Capability unavailable | Connect another backend |

## Agent Workflow

```bash
# 1. Check what's available
clinstagram --json auth status

# 2. Probe capabilities
clinstagram --json auth probe

# 3. Preview before acting
clinstagram --dry-run --json post photo https://example.com/img.jpg --caption "test"

# 4. Execute
clinstagram --json dm inbox --unread --limit 20

# 5. On error, read remediation field and execute it
```

## Growth Actions (Disabled by Default)

Follow, unfollow, like, unlike, comment add/reply require `--enable-growth-actions`. This is a safety gate — confirm with user before enabling.

## Backend Capability Matrix

| Feature | graph_ig | graph_fb | private |
|---------|:--------:|:--------:|:-------:|
| Post | Y | Y | Y |
| DM inbox | - | Y | Y |
| Cold DM | - | - | Y |
| Stories | - | Y | Y |
| Comments | Y | Y | Y |
| Analytics | Y | Y | Y |
| Follow/Unfollow | - | - | Y |
| Hashtag | Y | Y | Y |

Preference order: `graph_ig` > `graph_fb` > `private`. Override with `--backend`.
Overrides are still checked against compliance mode.

## Examples

```bash
# Check DMs
clinstagram --json dm inbox --unread

# Reply to a message
clinstagram --json dm send 123456789 "Thanks!"

# Post a photo
clinstagram config mode private-enabled
clinstagram --json post photo /path/to/img.jpg --caption "Hello world"

# Get analytics
clinstagram --json analytics post latest

# Search users
clinstagram --json user search "coffee shops"

# Browse hashtag
clinstagram --json hashtag top photography --limit 10
```

## Config

File: `~/.clinstagram/config.toml`. Override dir with `CLINSTAGRAM_CONFIG_DIR` env var.
