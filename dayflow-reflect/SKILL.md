---
name: dayflow-reflect
description: 把 04-workflow/dayflow/daily/ 的 timeline 数据加工成日复盘 review 文件。作为 reflect skill 的前置——做 dayflow 数据源维度的复盘，未来 reflect 综合多源时读取此处。默认 target=昨天，手动触发 /dayflow-reflect [YYYY-MM-DD]。输出位置：04-workflow/dayflow/reviews/daily/YYYY-MM-DD.md。本 skill 由 Claude 执行（不是纯脚本）：硬指标用 compute.py 算，TL;DR 和信号识别由 Claude 生成。
allowed-tools: Bash Read Write
---

# dayflow-reflect Skill (v0.5)

把 dayflow timeline（已搬运到 `04-workflow/dayflow/daily/YYYY-MM-DD.md`）加工成日复盘 review 文件。

## 触发

- 显式 `/dayflow-reflect [YYYY-MM-DD]`，无参数 → 昨天（本地时区）
- 定时：每工作日 09:30（cron `30 9 * * 1-5`）+ self-heal 自动补周末

## 流程（Claude 执行）

### Step 1. 解析参数

如果用户显式给了日期参数（YYYY-MM-DD 格式），用它；否则计算"昨天"：

```bash
date -v-1d +%Y-%m-%d   # macOS
```

### Step 1.5. Self-heal 扫描（v0.5+）

**仅在无显式日期参数时触发**（即 target=昨天的默认场景）。

跑 target_day 之前，扫描最近 7 天找需要补跑的日期：

```bash
for i in 1 2 3 4 5 6 7; do
  D=$(date -v-${i}d +%Y-%m-%d)
  DAILY="$HOME/linkc-os/04-workflow/dayflow/daily/${D}.md"
  REVIEW="$HOME/linkc-os/04-workflow/dayflow/reviews/daily/${D}.md"
  if [ -f "$DAILY" ] && [ ! -f "$REVIEW" ]; then
    echo "$D"
  fi
done
```

把列出的日期构成 `catchup_list`。

**处理队列**：`[target_day, *catchup_list]`（先跑 target_day，后补旧的）。

对队列中**每个日期**跑一遍 Step 2-7。

规则：
- `target_day` 永远跑（即使 review 已存在 → 覆盖）
- `catchup_list` 仅含 review 缺失的日期，避免重复劳动
- 显式 `/dayflow-reflect YYYY-MM-DD` 时**不触发** self-heal——只处理指定日期
- 如果 `catchup_list` 为空，行为等同 v0.4 默认（仅跑 target_day）

典型场景（周一开机）：
- target_day = 周日（昨天）
- catchup_list = [周五、周六]（daily 存在但 review 缺失）
- 实际执行：周日 → 周五 → 周六，共生成 3 份 review

### Step 2. 调 compute.py 拿卡片清单 + 时间结构信号

```bash
python3 ~/linkc-os/.claude/skills/dayflow-reflect/compute.py \
  ~/linkc-os/04-workflow/dayflow/daily/{date}.md
```

返回的 JSON 包含：
- `cards[]`：每张卡的完整数据（start/end/duration_min/title/summary/dayflow_category）
- `card_count`, `total_minutes`
- `categories_dayflow`：Dayflow 原始 category 计数（**仅作参考，最终类目由 Claude 重判**）
- `focus_blocks_dayflow[]`：基于 Dayflow category 的连续块（**仅作参考**）
- `long_blocks_2h`, `heavy_switch_hours`, `title_recurrence`：辅助信号
- `large_gaps`：透传自 daily frontmatter

`Processing failed` 的卡片已被自动过滤（Dayflow 自身分析失败的噪音）。

如果 JSON 解析失败或 daily 文件不存在 → 报错退出，提示用户先跑 `/dayflow-ingest {date}`。

### Step 3. 给每张卡片重新分类（10 类体系）

**Claude 拿到 cards 数组后，给每张卡片打一个类目标签**。10 个类目：

| # | 类目 | 涵盖 |
|---|---|---|
| 1 | 内容创作 | 写帖、剪视频、字幕、拍摄、设计、播客制作 |
| 2 | 技术开发 | 写代码、配置工具、调试、AI 智能体开发 |
| 3 | 调研学习 | **有目标 / 服务于某产出**的输入：政策研究、技术文档、为帖子做调研 |
| 4 | 专业探索 | **无明确产出但专业相关**：AI 新闻、技术 demo、工具评测、行业趋势 |
| 5 | 商务沟通 | **有决策 / 行动产出**的对接：协调嘉宾、业务策略、合作协议 |
| 6 | IM/视频会议 | 即时通讯（工作群、信息同步）、视频会议本身（Zoom/飞书/钉钉视频） |
| 7 | 项目管理 | 发布、归档、账号管理、整理项目文件 |
| 8 | 碎片浏览 | feed 式消费：小红书、X、刷短视频 |
| 9 | 长内容娱乐 | 完整叙事视频：电影、剧集、相声、动画、喜剧 |
| 10 | 个人事务 | 家人 / 个人 IM / 闲聊、休息、健康、Idle、行程机票 |

#### 边界规则（重要）

**Q1 多主题混合卡 → 取最主要的一个**
看 summary 内容占比 / 时长投入，选权重最高的活动。例如 "配置 Dayflow + AI 监管研究 + 悟空视频剪辑" 三主题混合，按摘要中各活动篇幅决定主导（如视频剪辑占多数 → 内容创作）。

**Q2 忽略 Dayflow 标签，纯按内容主题判断**
不要参考 `dayflow_category` 字段做最终判断。例：5-03 看加那利旅游视频 Dayflow 标 Distraction，但内容主题是行程相关 → 个人事务。

**Q3 取主要活动**
含工具但本质是消费的卡（如"ChatGPT 生图测试与 YouTube 浏览"），按摘要内容占比判断主活动。

#### 调研 vs 专业探索（最常见混淆）

- **调研学习**：能连接到当前正在做的产出（项目 / deadline / 帖子）
  - 例：写"Agent 与 VC 时代帖文"前研究 AI 工具
- **专业探索**：开放式输入，无具体产出但内容专业相关
  - 例：刷 X 看 AI 动态、看 ChatGPT Atlas 评测

判断方法：能否在当天 timeline 里找到对应的"产出卡"？有 → 调研；无 → 专业探索。

#### 商务沟通 vs IM/视频会议

- **商务沟通**：有决策 / 行动产出（"协调播客嘉宾"、"业务定价讨论"）
- **IM/视频会议**：纯信息同步、工作闲聊、视频会议载体（"查看微信职场资讯"）

#### IM 工作 vs 个人

- 与同事 / 客户 / 委托方 → IM/视频会议
- 与家人 / 朋友 / 行程闲聊 → 个人事务

#### 跨界活动速查（陈言确认的规则，必查）

- **写文档**（含数据字典、技术文档、Wiki 文章） → 技术开发
- **数据分析 / 报告整理 / 访谈记录整理 / 业务报告复盘** → 调研学习
- **群协作 / 业务讨论 / 嘉宾招募讨论 / 项目策略讨论** → IM/视频会议
- **整理项目文件 / 文件归档 / 整理联系人** → 项目管理

#### 商务沟通（收窄定义）

仅指**已下决定 / 已签约 / 已确认动作**的对接。例：
- ✓ "今天协调好播客嘉宾下周到位"（已确认）
- ✓ "签订合作协议"
- ❌ "群里讨论嘉宾招募方向" → IM/视频会议
- ❌ "讨论 AI 业务策略" → IM/视频会议
- ❌ "讨论身份验证方案" → IM/视频会议

#### 碎片浏览 vs 长内容娱乐

- 按内容形态：feed 列表 / 短视频 → 碎片浏览
- 完整视频（电影、相声、动画一集等） → 长内容娱乐

#### 长视频内容一律归"长内容娱乐"——不论主题

陈言纠正过的规则（重要）：**只要是看长视频，不论内容主题（旅游 / 时政 / 时事评论 / 风景 / 户外探索 / 电影 / 相声 / 动画），都归长内容娱乐**。

- ❌ 不要因为"旅游视频与新加坡行程相关"就归个人事务
- ❌ 不要因为"时政评论 / 空难调查 / 时事新闻"内容专业相关就归专业探索
- ✓ "个人事务"应限于：家人 / 个人 IM、行程规划的搜索动作（非视频）、休息 / Idle、机票讨论等
- ✓ "专业探索" 应限于：技术 / AI / 工具相关的非视频开放式输入（如刷 X 看 AI 动态、读 AI 新闻文章）

判断逻辑：先看**形态**（是不是长视频）→ 是 → 归长内容娱乐；不是 → 再看主题判类目。

### Step 4. 读 daily 文件原文（辅助上下文）

```bash
cat ~/linkc-os/04-workflow/dayflow/daily/{date}.md
```

主要用于信号段语义识别（distraction sub-events、返工识别）。分类已在 Step 3 完成。

### Step 5. 计算新指标（基于 Step 3 的分类）

完成每张卡的分类后，计算：

- **category_minutes**：每个类目的总时长（按卡片 duration_min 求和）
- **category_card_count**：每个类目的卡片数
- **new_focus_blocks**：连续 ≥30 min 同**新类目**的块（≤5 min 间隙允许跨卡片合并）
- **long_blocks_2h_new**：≥2h 同新类目块（new_focus_blocks 的子集）

### Step 6. 生成 review 内容

#### 6.1 TL;DR（2-3 句话）

格式：`{周X 工作日|周末}，{主轴或主题摘要}，{1 个突出观察}。`

要求：
- **不空泛**——具体到主题名（如"VC 技能开发"、"悟空视频剪辑"），不写"做了一些工作"
- **不评判**——不用"高效""分散"等主观词
- 限制 100 字以内

例：`周三工作日，全天主轴是 VC 技能开发（4 张卡 2h+）与悟空视频剪辑导出穿插推进。`

#### 6.2 注意力分布（按新 10 类）

```markdown
## 注意力分布
- {类目} {N} 卡 ({pct}%) · {minutes} 分钟
- ...（按时长降序，0 卡的类目不显示）
```

百分比按时长算（不是卡片数），更接近实际投入。

#### 6.3 工作块（按新分类）

只渲染产出 / 协作类目的连续块（**包括**：内容创作 / 技术开发 / 调研学习 / 专业探索 / 商务沟通 / IM/视频会议 / 项目管理 / 个人事务）。
**不渲染** 碎片浏览 / 长内容娱乐 块（即使长度超阈值，那不是 focus）。

```markdown
## 工作块（连续 ≥30 min 同类目）
- {start}–{end} [{类目}] ({duration} min)：{合并 card_titles 摘要为一句}
```

如果有 long_blocks_2h_new，对应行末尾标 `★ 长连续块`。

#### 6.4 信号段（Claude 语义识别）

只允许以下 3 类信号（去掉了"大空白"，按你的反馈），每类独立段落，没有就不写：

**返工**：识别同主题在不连续时段反复出现。结合 `title_recurrence` 粗筛 + 读 timeline 的语义判断。

**频繁切换**：1 小时内类目切换 ≥3 次（基于新分类重新计算）。

**长连续块**：≥2h 同类目块。不评判好坏，只标记。

**类目混淆提示**：如某张卡 Dayflow 标了 X 但 Claude 重判为 Y（且差异显著），可在信号段简短点出，便于陈言审视。

如果信号全空，写 `（本日无突出信号）`。

### Step 7. 拼装 + 写入

输出文件：`~/linkc-os/04-workflow/dayflow/reviews/daily/{date}.md`

模板：

```markdown
---
type: review
period: daily
date: {date}
weekday: {周中文}
source: dayflow-reflect
card_count: {N}
total_minutes: {N}
distraction_count: {N}        # 透传自 daily.total_distractions
large_gap_count: {N}          # len(large_gaps)
category_minutes:
  内容创作: {N}
  技术开发: {N}
  调研学习: {N}
  专业探索: {N}
  商务沟通: {N}
  IM/视频会议: {N}
  项目管理: {N}
  碎片浏览: {N}
  长内容娱乐: {N}
  个人事务: {N}
---

# {date} 日复盘 (Dayflow)

## TL;DR
{Step 6.1 的内容}

## 注意力分布
{Step 6.2 的渲染（按时长降序，0 分钟类目省略）}

## 工作块（连续 ≥30 min 同类目）
{Step 6.3 的渲染}

## 信号
{Step 6.4 的内容}

---
*Source: [[../../daily/{date}|原始 timeline]]*
```

文件已存在 → 直接覆盖（review 不需要保留人工区，那是 daily 文件的事）。
frontmatter 的 `category_minutes` 即使某类目为 0，也保留字段（值为 0），方便 reflect 后续按字段做月度趋势。

### Step 8. 控制台输出

打印 TL;DR 段给陈言，并提示文件路径：

```
✅ dayflow-reflect {date}
   {TL;DR 内容}
   写入: 04-workflow/dayflow/reviews/daily/{date}.md
```

## 不该做的事

- ❌ 不写 02-wiki/reviews/daily/（那是综合 reflect 的家）
- ❌ 不修改 04-workflow/dayflow/daily/ 下的文件（只读）
- ❌ 不调 reflect skill 接通——v0.1 是孤立的
- ❌ 不评判好坏 / 不给建议 / 不推测情绪（reflect 该做的事）
- ❌ 不读 journal / log（v0.1 仅 dayflow 单源，避免越界）

## 错误处理

- 目标日 daily 文件不存在 → 提示 `先跑 /dayflow-ingest {date} 再回来`
- compute.py 失败 → 输出错误信息，退出（不写 review 文件避免半成品）

## 已完成版本

- v0.1 核心搬运（硬指标 + LLM 信号）
- v0.2 取消 partial → 大空白作为元数据
- v0.3 10 类分类体系 + 边界规则
- v0.4 上定时（每日 09:00，dayflow-reflect-daily cron 任务）
- v0.5 Self-heal 扫最近 7 天补缺失 review + cron 改为工作日 09:30

## 后续版本

- v0.6 接通 reflect skill（reflect 读 04-workflow/dayflow/reviews/ 作为输入源之一）
- v0.7 周 / 月度 review（聚合多个 daily review）
- v0.8 趋势对比（vs 上周同一天 / 本周累计）
- v0.9 跨源关联（结合 journal、calendar、plaud）
