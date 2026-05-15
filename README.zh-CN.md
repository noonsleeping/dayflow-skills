[English](README.md) | **中文**

# dayflow-skills

> 把 [Dayflow](https://github.com/JerryZLiu/Dayflow) 桌面活动时间线搬运到你的 wiki，并附带 AI 日复盘 —— 两个 Claude Code skill。

---

## ⚠️ 依赖 Dayflow

这两个 skill 处理的是 **[Dayflow](https://github.com/JerryZLiu/Dayflow)** 产生的 SQLite 数据库。Dayflow 是 [@JerryZLiu](https://github.com/JerryZLiu) 开发的本地优先 macOS 桌面活动追踪工具——以 1 FPS 录屏，每 15 分钟由 LLM 分析一次，生成你做了什么的语义摘要。

**必须先安装并运行 Dayflow**——没有它就没有数据可处理。Dayflow 项目地址：<https://github.com/JerryZLiu/Dayflow>

---

## 包含内容

| Skill | 作用 | 文件 |
|---|---|---|
| **dayflow-ingest** | 从 Dayflow SQLite 提取 timeline 卡片到结构化日笔记。幂等写入、最近 7 天自我修复、schema 漂移检测、`--all` 批量回填。 | `SKILL.md` + `ingest.py` |
| **dayflow-reflect** | 基于已提取的 timeline 生成 AI 日复盘。10 类注意力分类、连续工作块识别、信号识别（返工 / 频繁切换 / 长连续块）。Self-heal 自动补齐最近 7 天缺失的 review（v0.5）。 | `SKILL.md` + `compute.py` |

两个 skill 协同工作：**ingest** 把 Dayflow SQLite 数据搬到原始日笔记；**reflect** 消费这些笔记生成结构化日复盘。

---

## 为什么需要这些 skill

Dayflow 已经做了重活——本地录屏、用 LLM 总结你实际做了什么。但输出留在 Dayflow 自己的 SQLite 里。这两个 skill：

1. 把数据搬到 **你拥有、可以 grep 的纯文本 wiki**
2. 在上面加一层 **AI 日复盘**——按 10 种注意力类型分类时间投入，识别专注块和返工模式

设计目标是作为 **[LinkcOS](https://github.com/noonsleeping/linkc-os)**（AI 维护的个人 wiki）的"外部数据源"标准模式，但也可以独立使用——路径常量改一下即可。

---

## 安装

这是 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill。选用户级或项目级：

**用户级（所有项目可用）：**

```bash
git clone https://github.com/noonsleeping/dayflow-skills.git
cp -r dayflow-skills/dayflow-ingest ~/.claude/skills/
cp -r dayflow-skills/dayflow-reflect ~/.claude/skills/
```

**项目级（LinkcOS 推荐方式）：**

```bash
cp -r dayflow-skills/dayflow-{ingest,reflect} <你的项目>/.claude/skills/
```

重启 Claude Code，两个 skill 会出现在 skill 列表里。

### 默认路径

默认假设 wiki 在 `~/linkc-os/`。如果你的 wiki 在别处，修改 `dayflow-ingest/ingest.py` 顶部的路径常量：

```python
WIKI_ROOT = HOME / "linkc-os" / "04-workflow" / "dayflow"
WIKI_LOG = HOME / "linkc-os" / "02-wiki" / "log.md"
```

未来版本可能加入 `LINKCOS_ROOT` 环境变量配置。

### Dayflow 数据库路径

默认：`~/Library/Application Support/Dayflow/chunks.sqlite`（自动检测）。覆盖：

```bash
DAYFLOW_DB_PATH=/custom/path/chunks.sqlite python3 ingest.py
```

---

## 使用

### dayflow-ingest

```bash
# 提取昨天 + self-heal 最近 7 天（默认）
python3 dayflow-ingest/ingest.py

# 指定日期
python3 dayflow-ingest/ingest.py 2026-05-06

# 一次性批量回填：SQLite 中所有 distinct day
python3 dayflow-ingest/ingest.py --all
```

或在 Claude Code 中：`/dayflow-ingest [YYYY-MM-DD]`

**输出：** `04-workflow/dayflow/daily/YYYY-MM-DD.md`——每天一个文件，timeline 卡片渲染为 `### HH:MM–HH:MM · 标题 [类目]` 块。幂等写入：AUTO 标记区被覆盖，标记外（人工注释）保留。

### dayflow-reflect

在 Claude Code 中：`/dayflow-reflect [YYYY-MM-DD]`（默认昨天）

skill 由 Claude 驱动——读 daily ingest 文件，按 10 类（内容创作 / 技术开发 / 调研学习 / 专业探索 / 商务沟通 / IM/视频会议 / 项目管理 / 碎片浏览 / 长内容娱乐 / 个人事务）重新分类每张卡，计算 focus blocks，生成日复盘。

**输出：** `04-workflow/dayflow/reviews/daily/YYYY-MM-DD.md`——含 `category_minutes` 时长分布的 frontmatter、工作块、Claude 生成的 TL;DR 和信号段。

### 定时任务（可选）

完全自动化可注册 cron 任务，通过 Claude Code 的 `mcp__scheduled-tasks`（或 [/schedule](https://docs.anthropic.com/en/docs/claude-code) skill）：

| 任务 | Cron | 作用 |
|---|---|---|
| `dayflow-ingest-daily` | `0 9 * * 1-5` | 工作日提取昨天 + self-heal 最近 7 天 |
| `dayflow-ingest-verify` | `15 9 * * 1-5` | 工作日校验，必要时重跑 |
| `dayflow-reflect-daily` | `30 9 * * 1-5` | 工作日生成昨天的日复盘 + self-heal 补最近 7 天缺失的 review（v0.5）|

**工作日（周一-周五）调度** —— 适用于周末不一定开机的工作模式。周一早上 reflect 任务通过 self-heal 自动补齐上周六/周日的 review。

---

## 输出结构

```
04-workflow/dayflow/
├── daily/
│   └── YYYY-MM-DD.md            ← 原始日笔记（dayflow-ingest）
├── reviews/
│   └── daily/
│       └── YYYY-MM-DD.md        ← AI 日复盘（dayflow-reflect）
└── meta/
    ├── ingest-log.md            ← 状态总览 + 运行历史
    └── schema-snapshot.md       ← Dayflow SQLite schema 快照（用于漂移检测）
```

---

## 许可

[MIT](LICENSE) © 2026 Linkc-Chen (陈言)

Dayflow 独立授权，详见 [JerryZLiu/Dayflow](https://github.com/JerryZLiu/Dayflow)。

---

## 致谢

- **[Dayflow](https://github.com/JerryZLiu/Dayflow)** by [@JerryZLiu](https://github.com/JerryZLiu)——使一切成为可能的上游
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** by Anthropic——skill 执行运行时
- **[LinkcOS](https://github.com/noonsleeping/linkc-os)**——这两个 skill 设计目标对应的个人 wiki 框架
