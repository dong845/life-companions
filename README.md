# Life Companion（`life-companion` · AI 陪伴 skill）

一个「越用越懂你」的私人陪伴 skill。它记得你（一份**只存在你本机**的档案 + 日记），
并从四个角度陪你：**命理命盘 · 每日运势 · 工作匹配 · 恋爱反思**。

贯穿始终的一条底线——**忠实计算，谦逊解读**：真实的系统（八字、O*NET 职业数据…）
忠实地算，算出来的是可复现的事实；怎么解读只是一面帮你自我反思的镜子，**不是科学预测**。
它不编造数字，不下判决，不给医疗/财务/法律建议，遇到危机会切成人的温度并给真实求助渠道。

---

## 怎么用

直接像聊天一样说话就行——不用记命令。第一次用会有一段简短的、征得同意的建档。

| 你想要… | 这样说（示例） | 会触发 |
|---|---|---|
| 看命盘 | 「帮我看看八字，1993-04-12 早上 7:35，男，北京」 | 命理命盘 |
| 每天的运势 / 记一天 | 「记一下今天，也看看今天运势」「今天有点丧…」 | 每日运势 + 日记 |
| 想清楚职业方向 | 「我不确定自己适合做什么工作」 | 工作匹配 |
| 理清一段关系 | 「跟对象闹别扭了，怎么办」 | 恋爱反思 |
| 就是想聊聊 | 「帮我梳理一下最近」 | 日记 + 陪伴 |

四个模块相互独立，一次只会用你需要的那一个。

### 四个模块

- **命理命盘（八字 · 星盘 · 紫微 · 合婚）** — 真实算四柱/日主/五行/十神/大运/流年（`lunar_python`
  计算 + `sxtwl` 独立核验立春边界）；西洋本命盘走真实星历；**紫微斗数**按标准安星法起盘（十二宫/
  命身宫/五行局/十四主星/六吉六煞/生年四化——但没有第二个引擎可交叉核验，会如实说明）；
  **合婚**只算传统的地支关系，**刻意不给「合/不合」的结论、分数或建议**。解读是**分层**的：一句话画像 → 大白话性格 → 分层面（事业/财/感情/
  健康/家庭/学业/性格）→ 分阶段人生时间轴（每步大运）。术语第一次出现就用大白话解释。
- **每日运势 + 日记** — 结合今天的流年/流月/流日 + 你的日记，给一段**短**的、贴着你近况的
  当天基调 + 一个温柔的宜/忌。不给幸运数字/颜色/评分。顺手把当天记进日记。
- **工作匹配** — 一个基于 Holland/RIASEC 的兴趣小测（21 题），对 **188 个真实 O*NET 职业**
  （CC BY 4.0）算契合度，给 **低/中/高** 档位 + 置信度，**不给假百分比**。其中 **68 个带真实数值兴趣分**
  （1–7，O*NET DB 30.3）、**62 个还带工作价值观**（DB 30.2）——所以加上价值观排序后是真·数据加权、更精准；
  要写简历会转给你的 `job-application` 技能。
- **恋爱反思** — 用依恋 / Gottman / NVC 等真实框架当镜子：分清事实与脑补、两边都站、命名模式、
  给具体动作（修复话/NVC 句/该问对方什么）。会**按人追踪**，跨事件记住模式。安全优先——识别到
  胁迫控制/暴力会切成安全模式，绝不 both-sides，给真实求助渠道。

---

## 你的数据在哪、怎么删

- 全部存在 **`~/.companion/`**（`chmod 700`，只有你能读，**从不上传任何地方**）：
  - `profile.yaml` 档案 · `consent.yaml` 同意记录 · `journal/` 日记（月度 `.md` + `index.jsonl`）
  · `state/` 命盘缓存 + continuity + 按人追踪
- **同意分类、可撤销**：生辰、感情、情绪各自单独授权；没授权就不收集、不推断、不存储。
- **随时真删除**（对它说就行，会真的删文件）：
  - 「删掉我的生辰数据」→ `companion.py forget --birth`
  - 「忘掉六月」→ `companion.py forget --month 2026-06`
  - 「全部清空」→ `companion.py forget --all --yes`
- 全部计算都是**离线**的（八字、职业匹配都不联网），所以你的数据不会离开这台机器，也不产生 API 费用。

---

## 技术备忘（给想改它的你）

```
SKILL.md              路由（始终加载）
AGENTS.md             给非 Claude agent（Codex 等）的入口说明
references/           onboarding · profile-schema · journaling · continuity · forms · factcheck · safety(始终生效)
  modules/            destiny · daily-fortune · career · relationships
scripts/              companion.py · bazi.py · astro.py · ziwei.py · synastry.py · career_match.py
                      relationship_patterns.py · safety_scan.py · trends.py · form_server.py
                      selfcheck.py · _deps.py
data/content/         bazi-interpretation · bazi-life-arc · relationships（真实框架内容层，可编辑）
data/career/          occupations.json（188 真实 O*NET，68 带数值兴趣分/62 带价值观，CC BY 4.0）· assessment_items.json（21 题）
tests/                回归测试（纯 unittest，离线）
```

- 依赖：`PyYAML` · `lunar-python`(MIT，八字) · `pyswisseph`(星盘) · `sxtwl`(BSD，立春交叉核验，可选)。
  缺失时脚本会尝试自动 pip 安装；装不上（无网络 / PEP 668 externally-managed）会打印明确的安装
  命令和「少了它会怎样」，而不是抛栈。`python3 scripts/companion.py doctor` 一次看全。
- 自测：`python3 tests/test_scripts.py`（45 项，约 7 秒，全离线）；`python3 scripts/career_match.py --selftest`。
- **发出去之前过一道闸门**：`python3 scripts/selfcheck.py --module destiny --file draft.md`
  ——机器检查编造的百分比/星级、宿命句式、临床标签、**编造的求助热线号码**、缺失的免责声明、
  没夹注的十神、以及缺事实核查块的高风险结论。退出码 1 = 别发。
- 解读内容都在 `data/content/`——想调口吻/加细节，改那里，不用动脚本。
- 诚实/安全底线在 `references/safety.md`，**优先于任何模块和任何"别加免责声明"的要求**。

---

*算出来的是事实，读出来的是镜子。真正做决定的人，永远是你。*
