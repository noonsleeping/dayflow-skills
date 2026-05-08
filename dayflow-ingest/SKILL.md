---
name: dayflow-ingest
description: 把 Dayflow（macOS 桌面活动追踪工具）的 timeline 卡片从本地 SQLite 提取到 04-workflow/dayflow/daily/。手动触发 /dayflow-ingest [YYYY-MM-DD]，无参数则提取昨天。每次运行自动扫描最近 7 天 self-heal（重试 failed 和不在日志的日期）。幂等写入：AUTO 标记内覆盖，标记外（人工注释）保留。本 skill 只做数据搬运，不做语义加工——大空白等"是否异常"的判断留给后续 reflect skill。
allowed-tools: Bash Read
---

# Dayflow Ingest Skill (v0.3)

把 Dayflow 桌面活动数据从本地 SQLite 搬运到 LinkcOS `04-workflow/dayflow/daily/`。

## 触发条件

- **每日 07:30** 自动跑 — `dayflow-ingest-daily` 定时任务（cron `30 7 * * *`）
- **每日 08:30** 自动校验 — `dayflow-ingest-verify` 定时任务（cron `30 8 * * *`）
- 手动 `/dayflow-ingest [YYYY-MM-DD]` 或直接跑 `python3 ingest.py [date]`
- 无参数 → 默认提取**昨天**（本地时区 Asia/Shanghai）

定时任务通过 `mcp__scheduled-tasks` 创建，依赖 Claude Code 应用打开 + 电脑唤醒。如某天因休眠未触发，下次任意运行的 self-heal 机制会自动补齐。

## 执行流程

每次调用就跑一次 `ingest.py`：

```bash
# 提取昨天 + 自我修复最近 7 天
python3 ~/linkc-os/.claude/skills/dayflow-ingest/ingest.py

# 提取指定日期 + 自我修复
python3 ~/linkc-os/.claude/skills/dayflow-ingest/ingest.py 2026-05-06
```

脚本内部流程：
1. 写 `meta/schema-snapshot.md`（首次）+ 检测 schema 漂移（不阻塞）
2. 读 `meta/ingest-log.md` 解析现有状态总览
3. 处理 **target 日**（run_type=manual）：
   - 查 SQLite `WHERE day='YYYY-MM-DD' AND is_deleted=0 ORDER BY start_ts ASC`
   - 提取 gap 元数据（>2h 的相邻空白记录到 frontmatter，**不当作问题**）
   - 渲染 markdown + 幂等写入 daily/YYYY-MM-DD.md
4. **Self-heal**：扫描最近 7 天（不含 target），对 `failed/不在日志` 状态重试（run_type=self-heal）
5. 整表重写 `meta/ingest-log.md` + 追加运行流水
6. 在 `02-wiki/log.md` 加 1 行汇总（含 self-heal 摘要 + schema drift 标记）

## 状态码

| 状态 | 触发条件 |
|---|---|
| `complete` | 查到卡片，写入成功 |
| `empty` | SQL 返回 0 条（Dayflow 当天无数据） |
| `failed` | DB 不存在 / 查询报错 / 写入失败（self-heal 时重试计数 +1） |

**没有 partial 状态**。relevant 设计原则：ingest skill 只做忠实搬运，**不判断"这天数据是否异常"**——因为陈言出差/周末/正常作息会产生各种长度的空白，需要 reflect skill 拿日历/周几等上下文才能正确判断。

## Gap 元数据

frontmatter 永远记录：
- `largest_gap_min`: 当天相邻卡片间最大空白（分钟）
- `large_gaps`: 所有 >2h 的 gap 列表（含起止时间和分钟数），无则为 `[]`

这些是给后续 reflect skill 消费的元数据。备注列在 ingest-log overview 也会附信息提示（如"含 2 段 >2h 空白（最长 240 分钟）"），但不当作问题标记。

## Self-heal 行为

每次运行扫描最近 7 个自然日：

| 日志中状态 | 行为 |
|---|---|
| `complete` / `empty` | 跳过 |
| `failed` | 重新查询，重试计数 +1 写入备注 |
| 不在日志中 | 视为 pending，执行单日提取 |

成功（complete/empty）时清空重试计数。

## Schema 漂移

每次运行抓 `timeline_cards` 表的当前 SQL，与 `meta/schema-snapshot.md` 比对：
- 一致 → 不做事
- 不一致 → 在 ingest-log 运行流水加 ⚠️ 行 + wiki/log.md 标 `⚠️ schema drift`，**不阻塞**主流程

发生时陈言需要人工介入：检查 Dayflow 是否升级、手动更新 snapshot、必要时调整 ingest 脚本字段映射。

## 回报格式

成功：
```
✅ dayflow YYYY-MM-DD: N cards (HH:MM–HH:MM)
   写入: 04-workflow/dayflow/daily/YYYY-MM-DD.md
   分类: Work N · Distraction N · Idle N · 打断 N
```

空集：
```
⏭️ dayflow YYYY-MM-DD: empty (当天 Dayflow 无数据)
```

失败：
```
❌ dayflow YYYY-MM-DD: failed
   原因: <具体错误信息>
```

## 不该做的事

- ❌ 修改 Dayflow 数据库（只读访问）
- ❌ 删除任何文件（包括旧 daily note）
- ❌ 覆盖 AUTO 标记外的人工注释
- ❌ 静默失败——任何错误必须在 ingest-log.md 运行流水留痕
- ❌ 在 v0.1 自动跑 self-heal（v0.2 才加）
- ❌ 修改 `01-raw/`、`02-wiki/`（除 `02-wiki/log.md` 加一行摘要）、`03-schema/`

## 相关文件

- `04-workflow/dayflow/README.md` — 数据来源、字段映射、跑法
- `04-workflow/CLAUDE.md` — 本数据层定位
- `04-workflow/dayflow/meta/ingest-log.md` — 运行历史
- `04-workflow/dayflow/meta/schema-snapshot.md` — 源 schema 快照（v0.2 用于漂移检测）

## 后续版本

- **v0.4** 鲁棒性：sqlite 锁重试 3 次（间隔 5s）、AUTO 标记被人工破坏的容错

## 定时任务管理

```bash
# 查看任务状态
# (在 Claude Code 里调用 mcp__scheduled-tasks__list_scheduled_tasks)

# 任务文件位置
~/.claude/scheduled-tasks/dayflow-ingest-daily/SKILL.md
~/.claude/scheduled-tasks/dayflow-ingest-verify/SKILL.md
```

如要暂停/修改：通过 Claude Code 侧边栏的 "Scheduled" 区域操作，或调用 `mcp__scheduled-tasks__update_scheduled_task`。
