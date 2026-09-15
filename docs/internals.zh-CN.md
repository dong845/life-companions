# life-companion：内部是怎么工作的

这里是工程记录：四个模块的细节、文件地图、依赖、测试和诚实闸门。**用** life-companion 不需要读这些，[README](../README.zh-CN.md) 已经讲清楚了。

---

## 四个模块的细节

**命理命盘 —— 八字 · 西洋星盘 · 紫微斗数 · 合婚**
四柱、日主、五行、十神、大运、流年，全部真算（`lunar-python` 计算，`sxtwl` 独立核验
立春这条年柱边界）。西洋本命盘走真实瑞士星历，出生时刻不明时**宁可不给上升，也不猜**。
紫微斗数按标准安星法起十二宫、命宫身宫、五行局、十四主星和生年四化——但这里**没有第二个
引擎能交叉核验它**，这一点会写在输出里如实告诉你。合婚只算两盘之间传统的地支关系，
**刻意不给「合／不合」的结论、分数或建议**：一句「你俩不合」拆散过本来好好的关系。

解读是**分层**的：一句话画像 → 大白话性格速写 → 分层面（事业／财／感情／健康／家庭／
学业／性格）→ 分阶段的人生时间轴。术语第一次出现就带大白话夹注。

**每日运势 + 日记**
把今天的流年／流月／流日，和你日记里真实写下的东西织在一起，给一段**短**的当天基调，
外加一个温和的宜／忌。不打星级，也不编幸运数字：颜色和数字只作为你八字喜用五行的传统对应出现，
并且标明是这个。顺手可以帮你把今天记下来。

**工作匹配**
一个透明的 21 题兴趣小测，底子是真实的 Holland/RIASEC 模型，对 **188 个真实 O\*NET
职业**（CC BY 4.0）算契合度，给 **低／中／高** 档位加一句置信度说明，**不给假百分比**。
188 个都带 O\*NET 31.0 的数值兴趣分，其中 **173 个**还带工作价值观，所以你补上价值观排序之后，
匹配是真的数据加权。`--find` 负责把你嘴里说的岗位（「核磁共振技师」「高中老师」）对到真实的
职业编码上；不是那份工作本身的，只标成「弱」的近邻，数据里没有的岗位（比如「MRI 重建」）不会被当成
匹配，也不拿最接近的顶替。要写简历会转给 `job-hunt` skill。

**恋爱反思**
拿依恋理论、Gottman、NVC 当镜子：先分开「实际发生了什么」和「你脑补的故事」，两边都站，
命名模式，然后给具体动作——一句修复的话、一个 NVC 句式、一个该直接问对方的问题。
它**按人追踪**，所以「又是这个循环」是站在真实记录上说的，不是凭印象。一次不叫模式。
安全永远优先：识别到胁迫控制或暴力会切成安全模式，绝不 both-sides，直接给专门的求助渠道。

---

## 技术备忘（给想改它的你）

```
SKILL.md              路由（始终加载）
AGENTS.md             给非 Claude agent（Codex 等）的入口说明
references/           onboarding · profile-schema · journaling · continuity · forms
                      factcheck · voice · safety（始终生效）
  modules/            destiny · daily-fortune · career · relationships
scripts/              companion.py · bazi.py · astro.py · ziwei.py · synastry.py
                      career_match.py · relationship_patterns.py · safety_scan.py
                      trends.py · form_server.py · selfcheck.py
                      _deps.py · _zh.py · _branches.py · _tz.py
data/content/         bazi-interpretation · bazi-life-arc · relationships
                      （真实框架的内容层，可直接编辑）
data/career/          occupations.json（188 个真实 O*NET，CC BY 4.0）· assessment_items.json
data/zh/              OpenCC 繁→简字表（Apache-2.0）：诚实闸门和危机扫描先把繁体转成简体再匹配
tools/                build_occupations.py：从 O*NET 数据库重建 data/career/occupations.json
                      （维护用，skill 运行时不会调用）
                      run_agent_evals.py：让 codex 在临时项目里逐个跑 evals/evals.json 的场景（维护用）
evals/                evals.json：run_agent_evals.py 跑的场景和各自的检查项
tests/                回归测试（纯 unittest，全离线）
docs/                 internals.md · internals.zh-CN.md（本页）· assets/hero.jpg
```

**依赖**：`PyYAML` · `lunar-python`（MIT，八字）· `pyswisseph`（星盘）·
`sxtwl`（BSD，立春交叉核验，可选）。缺失时脚本会尝试自动 pip 安装；装不上的时候
（没网络、PEP 668 externally-managed 的 Python）会打印明确的安装命令和「少了它会怎样」，
而不是甩一段栈。`python3 scripts/companion.py doctor` 一次看全。

**测试**：`python3 tests/test_scripts.py`（几百项，一两分钟，全程不联网）。
另有 `python3 scripts/career_match.py --selftest` 和 `python3 scripts/ziwei.py --selftest`。

**发出去之前过一道闸门**：
`python3 scripts/selfcheck.py --module destiny --file draft.md`

一条命令，两套互不相干的检查：

- **诚实**（会拦，退出码 1）：编造的百分比和星级、宿命句式（包括「大概率保不住」这种
  对冲过的）、对亲人健康的预言、黄历式禁令、预测录用结果、给不在场的一方贴临床标签、
  **编造的求助热线号码**、缺失的免责声明、没夹注的十神、没有来源和时效就抛出的高风险
  事实，以及拿一段解读去替现实决定背书。
- **AI 味**（永不拦）：让回复读起来像一张填好的表的那些**措辞**问题——「不是X，是Y」
  当反射、套话、破折号当默认连接词、句子长度过于均匀、一个断句都没有、填充副词。
  详见 `references/voice.md`，那里同时讲了怎么把中英文都写得通俗易懂。

**解读内容都在 `data/content/`**，想调口吻或加细节，改那里就行，不用动脚本。

**诚实与安全的底线在 `references/safety.md`**，它**优先于任何模块、也优先于任何
「别加免责声明」的要求**。
