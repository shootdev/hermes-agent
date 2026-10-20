---
title: "Qzhuli — QZhuli qz CLI: contacts, messages, conversations, QFlow"
sidebar_label: "Qzhuli"
description: "QZhuli qz CLI: contacts, messages, conversations, QFlow"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Qzhuli

QZhuli qz CLI: contacts, messages, conversations, QFlow.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/productivity/qzhuli` |
| Version | `0.1.0` |
| Author | shootdev (QZhuli) + Hermes Agent |
| License | MIT |
| Platforms | linux, macos |
| Tags | `qzhuli`, `qz`, `messaging`, `contacts`, `conversations`, `messages`, `qflow`, `requirements`, `CLI` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# QZhuli Skill

Operate QZhuli through the bundled `qz` CLI: user/friend/relation lookups,
conversations, messages, cache, and the separate QFlow requirement workflow.
The CLI returns JSON on stdout; it does not send anything until a write command
runs, and every write needs explicit user confirmation.

This skill covers the legacy QZhuli contact/message surface and QFlow. It does
not cover browsing the QZhuli web UI or administering accounts.

## When to Use

- Look up a QZhuli user by Q助号 or UID, list friends, or inspect/update a relation.
- List or search conversations, read history, or send a message as Assistant or User.
- List QFlow projects, read/create/comment/transition requirements, urge an owner.
- Sync or inspect the local cache before heavy reads.

## Prerequisites

- The `qz` CLI. The repo pre-installs `@qzhuli/qzhuli-cli`; call the bundled
  launcher `scripts/qz` (skill-relative) so it resolves PATH, the local
  `node_modules/.bin/qz`, or a pinned `npx` fallback without a global install.
- Login is per-surface and interactive: `scripts/qz auth login` shows a QR code
  the user scans with the QZhuli mobile app; `scripts/qz qflow auth login` does
  the same for QFlow. Never paste credentials into chat; never read or send
  `~/.qzhuli-cli/credentials.json` or `qflow-credentials.json` to context.
- Exact syntax for any subcommand: `scripts/qz <command> --help`.

## How to Run

Always go through the bundled launcher rather than assuming bare `qz` is on PATH:

```bash
# Resolves PATH qz, repo-local node_modules/.bin/qz, then pinned npx.
scripts/qz --version
scripts/qz auth status
```

Pass arguments through unchanged; the launcher adds nothing. Check login before
any read that needs it, and check QFlow auth separately before QFlow work.

## Quick Reference

| Goal | Command |
| --- | --- |
| Check login | `scripts/qz auth status` |
| QR login | `scripts/qz auth login` |
| Logout | `scripts/qz auth logout` |
| Preferences / locale | `scripts/qz config` / `scripts/qz config --locale zh` |
| Search user (Q助号) | `scripts/qz user search <q-number>` |
| Search user (UID) | `scripts/qz user search <uid> --uid` |
| Add friend | `scripts/qz user add <q-number>` |
| List friends | `scripts/qz friend list` |
| Friend profile | `scripts/qz friend profile "<nickname>"` / `<uid> --uid` |
| Read relation | `scripts/qz relation get <uid>` |
| Update relation | `scripts/qz relation set <uid> --remark "<name>" --type <n>` |
| List conversations | `scripts/qz conversation list [--limit <n>] [--offset <n>]` |
| Search conversation | `scripts/qz conversation search <q-number> --qnumber` |
| Create conversation | `scripts/qz conversation create <uid> --agent-id <id>` |
| Send message | `scripts/qz message send <conversation-id> "<text>" [--role <n>]` |
| Message history | `scripts/qz message history <conversation-id> [--limit <n>]` |
| QFlow login status | `scripts/qz qflow auth status` |
| QFlow projects | `scripts/qz qflow project list` |
| My requirements | `scripts/qz qflow requirement mine` |
| Requirement detail | `scripts/qz qflow requirement get <requirement-id>` |
| Create requirement | `scripts/qz qflow requirement create --project-id <id> --title "<t>" --priority <p> --category <c> --content "<body>"` |
| Comment | `scripts/qz qflow requirement comment <requirement-id> --content "<text>"` |
| Transition | `scripts/qz qflow requirement transition <requirement-id> --to <stage> --remark "<reason>"` |
| Urge owner | `scripts/qz qflow requirement urge <requirement-id>` |
| Cache sync/status/clear | `scripts/qz cache sync` / `scripts/qz cache status` / `scripts/qz cache clear` |

Global flags: `-q, --jq <expr>` filters a simple dot path (`.data`,
`.data.uid`); `--dry-run` prints the request without executing; `-v, --version`;
`-h, --help`.

## Procedure

### First checks

1. `scripts/qz auth status` — report logged-in nickname; if missing, run
   `scripts/qz auth login` and ask the user to scan the QR code.
2. For QFlow, also `scripts/qz qflow auth status`; run `scripts/qz qflow auth login`
   when needed.
3. Use `scripts/qz <command> --help` for exact flags rather than guessing.

### ID types (using the wrong one fails silently or hits the wrong target)

| ID type | Format | Example | Used by |
| --- | --- | --- | --- |
| Q助号 `id` | short number | `10003` | `user add`, `user search`, `conversation search --qnumber` |
| `uid` | 32-char hex | `d5b6308e3abad6bc96573c58` | `relation get/set`, `friend profile --uid`, `conversation create` |
| `conversationId` | base64-like string | `9boGaR7iii2Jdjhmb5LSo37...` | `message send/history`, `conversation profile/search` |
| `agent.id` | number | `5` | `conversation create --agent-id` |

QFlow uses a separate ID family: project `p-...`, requirement `r-...`, QFlow
user `u-...`, team `t-...`. The QFlow `u-...` user ID is not the legacy 32-char
`uid`.

### Send a message

1. Confirm auth with `scripts/qz auth status`.
2. Resolve an existing `conversationId` with `conversation list` or
   `conversation search`; never send to a UID directly.
3. If no conversation exists, ask the user before `conversation create`.
4. Role: `--role 1` (User) only when the user says "以我的名义 / 帮我发给 /
   替我发送" or equivalent; otherwise omit it (default Assistant `0`).
5. Send, then verify with `scripts/qz message history <conversation-id> --limit 5`.

### QFlow requirement lifecycle

Stages: `待完善`, `完善中`, `待审批`, `待领取`, `研发中`, `待验收`,
`验收中`, `已完成`, `已取消`. New requirements start at `待审批`; the CLI does
not execute `待审批 -> 待领取` (complete that step in QFlow), then continues
`待领取 -> 研发中 -> 待验收 -> 验收中 -> 已完成`. Before `create`, `update`,
`transition`, `assign-user`, `share`, `comment`, or `urge`, show the target
project/requirement and get confirmation; use `--dry-run` to preview writes.

### Safety rules

- Multiple matches (`needs_resolution`), friend adds, relation changes, cache
  clears, message sends without a known conversation, and every QFlow write:
  stop and ask first; show candidates with their identifiers.
- Never change a remark/relation/requirement field without showing old and new.
- Never use `--skip-similarity-check` silently; it bypasses duplicate detection.
- Check `status` in the JSON, not just exit code: `success` = 0,
  `needs_resolution` = 0 (ambiguous, refine), `error` = 1.

## Pitfalls

| Symptom | Action |
| --- | --- |
| `qz: command not found` | Use `scripts/qz`; it falls back to pinned npx. |
| Auth failure | `scripts/qz auth status`, then `scripts/qz auth login`. |
| QFlow auth fails | `scripts/qz qflow auth status`, then `scripts/qz qflow auth login`. |
| Wrong language | `scripts/qz config --locale en` or `--locale zh`. |
| Too much JSON | Add `-q .data` or another simple dot path. |
| Message send fails | Re-check auth and resolve `conversationId` via `conversation list`. |
| `relation set` INVALID_ARGUMENT | Supply at least one of `--remark` or `--type`. |
| Slow reads | Run `scripts/qz cache sync` first, then retry; `cache clear` resets to API fallback. |
| Requirement absent from `mine` | `mine` lists requirements owned by the user, not every one they created. |

## Verification

- `scripts/qz --version` prints `v0.5.12` and `scripts/qz auth status` returns
  `status: success` before relying on the CLI.
- After a write (message, relation change, QFlow create/transition), read the
  same entity back (`message history`, `relation get`, `qflow requirement get`)
  and confirm the change; never report a write as done from a preview or summary.
