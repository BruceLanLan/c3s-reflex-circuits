# 文案表 · `t()` 键（UX 评审 2026-09-16 · Fable 5.1）

只列会改的键。两种语言同步改；`HTML_KEYS` 里的键（`ap.lead`、`r.*.hint`、`ev.*.body` 等）走 `innerHTML`，
本表提案没有给任何键新增或删掉标签，`HTML_KEYS` 不需要变。所有面向用户的比喻只用「反射弧 / reflex arc」，
不出现「大脑 / brain」；页面今天两者都没有，本表也没引入。

约定：**写在屏幕这一侧的人**读得懂。一句话先说「这是什么 / 你能做什么」，工程名放到第二句或去掉。

## 导航（手机上 7 个标签要各自留下名字）

| key | 现中文 | 现英文 | 提案中文 | 提案英文 | 为什么 |
| --- | --- | --- | --- | --- | --- |
| `nav.overview` | Overview · 总览 | Overview | 总览 | Overview | 手机 400px 宽 7 个标签每个 53px，`Overview · 总览` 只能省略成 `Overvie…`（截图 opt-approvals-phone-zh-viewport.png）；中文界面里英文前缀不承担信息 |
| `nav.boundaries` | Boundaries · 边界 | Boundaries | 边界 | Boundaries | 同上 |
| `nav.approvals` | Approvals · 待确认 | Approvals | 待确认 | Approvals | 同上 |
| `nav.activity` | Activity · 流水 | Activity | 流水 | Activity | 同上 |
| `nav.connect` | Connect · 接入 | Connect | 接入 | Connect | 同上 |
| `nav.agents` | Agents | Agents | Agents | Agents | 不改：没有更短的中文，`Agents` 已是页面里的专名 |

> 这条偏离了画板（画板导航是双语）。画板是 1440 宽的桌面稿，双语在桌面上放得下；手机上放不下，
> 而 v1.0 手机是审批的主场景。`nav.tasks` 是今晚 Tasks 视图的键，归它的作者，这里不动。

## 待确认页（一个人能不能批对东西）

| key | 现中文 | 现英文 | 提案中文 | 提案英文 | 为什么 |
| --- | --- | --- | --- | --- | --- |
| `ap.lead` | 这里是 `confirm` 通道。模型碰不到这一页。列出来的是电路因为「缺一个人的一次点头」而拒绝的请求；点一下就是写一位，下一次请求会读到它。 | This is the `confirm` channel. The model cannot reach this page. Listed here are requests the circuit refused for want of one person's nod; a click writes one bit, and the next request reads it. | 这些调用在等你点头。模型看不到、也按不了这一页。你确认的只是那一条：agent 原样重发它，反射弧才放行；它先发别的，不会用掉你的确认。 | These calls are waiting for your nod. The model cannot see or press this page. A confirm covers one call only: the agent must send that exact call again for the reflex arc to grant it, and anything else it sends first does not spend it. | 原文是给懂「通道 / 写一位」的人写的；新文案先说人能做什么，再说确认的边界（只这一条） |
| `ap.pending` | 等一个人来点 | Awaiting a person | 等你点头 | Waiting for you | 屏幕这一侧就是「你」；「一个人」是系统视角 |
| `ap.confirm` | 确认一次 | confirm once | 确认这一条 | Confirm this one | 按钮上已经带两位数（`确认这一条 · 75`）；「一次」在中文里像次数，「这一条」把确认绑到卡片 |
| `ap.confirm_b` | 给第二把钥匙 | give the second key | 给出第二把钥匙 | Give the second key | 微调，句法一致 |
| `ap.block` | 封锁该 agent | block this agent | 停下这个 agent | Stop this agent | 与全局按钮「全部停下 / Stop everything」同一个动词；「封锁」是 blocked 位的工程译名 |
| `ap.code` | 匹配码 | matching code | 核对码 | check code | 「匹配」是算法词；人的动作是核对屏幕上的两位数 |
| `ap.code.hint` | 按钮上的两位数要和这里一样——同一块屏幕上读到的才算。聊天里批准必须带它。 | The two digits on the button must be the two digits here — read off the same screen as the request. A chat channel must send them. | 按钮上的两位数就是这条请求的号码：对得上，你批的就是它。在 Telegram 里批准要把这两位数一起发。 | The two digits on the button are this request's number: if they match, you are approving this one. Approving from Telegram means sending those two digits too. | 说清两位数存在的理由（防批错一条），而不是规则本身；并且建议只在列表顶部出现一次（见审计 F1），不是每张卡重复 |
| `ap.irrev.note` | 工具层把这条标成了不可逆；电路要一次未用掉的确认才放。确认只放这一次。 | The tool layer marked this call irreversible; the circuit wants one unspent confirm. A confirm grants this once. | 这一步做了就收不回来，所以反射弧要你先点头。确认只放行这一条；不想放，就什么也不点，或停下这个 agent。 | This step cannot be undone, so the reflex arc wants your nod first. A confirm grants this one call only; if you do not want it, press nothing — or stop this agent. | 告诉人「不批」也是选项，现在的文案只教怎么批 |
| `ap.key.note` | 缺第二把钥匙。 | The second key is missing. | 这一条要两个人各点一次。你按下的是第一把钥匙；第二把（confirm_b）由另一个人在他的屏幕上给。 | This one needs two people. Yours is the first key; the second (confirm_b) is given by the other person on their own screen. | 现文案不说谁持第二把钥匙；实测（studio:my-agent 的付款卡）按下「确认一次」后请求继续等待，页面没解释为什么 |
| `ap.break.note` | 熔断已跳。一次 confirm 复位。 | Breaker tripped. One confirm resets it. | 它连续失败太多次，反射弧把它停了。点确认就是「让它再试」；不确定就先看流水里它失败了什么。 | It failed too many times in a row, so the reflex arc stopped it. Confirming means "let it try again"; if unsure, look in Activity at what failed. | 说后果（再试）与下一步（去哪看），而不是机制名 |
| `ap.halt.note` | 停机中。一次 confirm 解除，解除那一拍本身不放行。 | Halted. One confirm lifts it; the lifting tick itself grants nothing. | 共享停机把它停了（被封锁过，或太久没有心跳）。点确认只是解除停机，这一条本身要它再发一次。 | The shared halt stopped it (it was blocked, or went too long without a heartbeat). Confirming only lifts the halt; this call itself needs to be sent again. | 现文案的「拍」对非开发者没有意义 |
| `ap.wait.note` | 这不是确认能解的——只能等。列在这里是让你知道它在等。 | Not something a confirm can fix — only waiting. Listed so you know it is waiting. | 这条不需要你做什么：它在等冷却或上限，agent 自己重发就行。列在这里只是让你知道。 | Nothing for you to do here: it is waiting on a cooldown or a limit and will pass when the agent sends again. Listed so you know. | 说清「不需要你」 |
| `ap.blocked.note` | 这个 agent 正被封锁：它的请求一律被拒。先解除封锁（右边开关面板，或下面的「解除全部封锁」），确认才有意义。 | This agent is blocked: everything it asks is refused. Lift the block first (the switches panel, or "lift every block" below) — a confirm cannot help while it stands. | 这个 agent 正停着，它的每一条都会被拒。只点确认没有用：先让它恢复——只恢复它，用手动开关里 blocked 的「放下」；全部恢复，用「恢复全部」。 | This agent is stopped and everything it asks is refused. A confirm alone does nothing: resume it first — this agent only, with "lower" on the blocked switch in Manual switches; every agent, with "Resume everything". | 与新的停/恢复用词一致；保留按 agent 恢复的路径（开关面板在手机上位于下方，不说「右边」） |
| `ap.cardputer` | Cardputer 插着并用 REFLEX_CARDPUTER=1 启动后台时：设备菜单 3 是这一页，Enter 就是「确认一次」，b/u 封锁/解封，s 让所有 agent 停下。设备拔掉时，装了心跳规则的停机电路会让所有 agent 停下。 | With a Cardputer plugged in and the console started with REFLEX_CARDPUTER=1: the device's agent page mirrors this one, Enter is "confirm once", b/u block and unblock, s stops every agent. Unplug it and a halt circuit with a heartbeat rule stops every agent. | Cardputer 插着并用 REFLEX_CARDPUTER=1 启动后台时：设备菜单 3 就是这一页，Enter 是「确认这一条」，b/u 停下/恢复该 agent，s 是「全部停下」。设备拔掉时，装了心跳规则的共享停机会让所有 agent 停下。 | With a Cardputer plugged in and the console started with REFLEX_CARDPUTER=1: the device's agent page mirrors this one, Enter is "Confirm this one", b/u stop and resume that agent, s is "Stop everything". Unplug it and a shared halt with a heartbeat rule stops every agent. | 跟着 `ap.confirm` / `ap.block` 的改名走，别让设备说明还引用旧按钮名 |
| `ap.stop.title` | 全部停下 | Stop everything | 全部停下 | Stop everything | 不改，好词 |
| `ap.stop.sub` | 一下把所有 agent 的 blocked 拉高；要解除得再做一次明确动作 | raises blocked for every agent at once; getting back needs a second, deliberate action | 按住半秒，所有 agent 立刻停下；恢复要另外做一次明确动作 | Hold for half a second and every agent stops at once; resuming is a separate, deliberate action | **只在审计 F4 的按住 JS 落地后再换**（`ap.stop.hold` / `ap.stop.hint` 同）；否则文案在说一个不存在的手势。去掉「拉高 blocked」 |
| `ap.stopped.banner` | 封锁生效中：{m}/{n} 个 agent 的下一次请求都会被拒。blocked 是电平，会一直保持，直到有人明确解除。 | Block in force: the next request from {m} of {n} agents is refused. blocked is a level — it stays until a person lifts it deliberately. | 已全部停下：{m}/{n} 个 agent 的下一条请求都会被拒。它不会自己恢复，要你在下面明确解除。 | Everything is stopped: the next request from {m} of {n} agents is refused. It does not lift by itself; you resume it below. | 「电平」是电路词；人要知道的是「不会自己恢复」 |
| `ap.resume.open` | 解除全部封锁 | lift every block | 恢复全部 | Resume everything | 与「全部停下」成对 |
| `ap.resume.go` | 确认解除 | confirm lift | 确认恢复 | Confirm resume | 同上 |
| `ap.resume.ask` | 解除是第二个动作：先点「解除全部封锁」，再输入 {word}，然后确认。故意麻烦。 | Lifting is a second action: press "lift every block", type {word}, then confirm. Deliberately awkward. | 恢复要三步：点「恢复全部」，输入 {word}，再确认。故意比停下麻烦。 | Resuming takes three steps: press "Resume everything", type {word}, then confirm. Deliberately harder than stopping. | 和新按钮名对齐；`ap.resume.word`（用户要打的词 解除 / RESUME）**不改**，改它会改手势 |
| `ap.switches` | 工具层的其他开关 | The tool layer's other switches | 手动开关（高级） | Manual switches (advanced) | 这块是开发者面板；标出来，非开发者可以跳过 |
| `ap.grey` | 灰掉的位是这个 agent 的电路都不读取的：面板不会提示一个电路忽略的输入。 | A greyed bit is one none of this agent's circuits read: the panel never suggests an input a circuit ignores. | 灰掉的开关这个 agent 的反射弧不读，按了也没用，所以不给按。 | A greyed switch is one this agent's reflex arc does not read; pressing it would do nothing, so it cannot be pressed. | 短一半 |
| `ap.noagent` | 还没有 agent。 | No agents yet. | 还没有 agent 接进来——去「接入」页接一个。 | No agent has connected yet — go to Connect. | 空状态给出口 |

## 总览与证据（让证据落地，不吹）

| key | 现中文 | 现英文 | 提案中文 | 提案英文 | 为什么 |
| --- | --- | --- | --- | --- | --- |
| `ov.circuits` | 四张电路 | Four circuits | 四条反射弧 | Four reflex arcs | 总览是给人看的第一页；「反射弧」是产品的比喻，「电路」留给边界页与证据细节 |
| `ov.circuits.sub` | 一类工具一张，行数相加不相乘 | one per class of tool; rows add, not multiply | 一类工具一条：花钱、发消息、执行、文件 | one per kind of tool: spend, message, exec, files | 「相加不相乘」是给评审看的话 |
| `ov.holds` | ✓ 每条规则成立 · 全域已核 | ✓ every rule holds · every row checked | ✓ 每一行都核过，每条规则都成立 | ✓ every row checked, every rule holds | 顺序改成人读的顺序：先「都核过」，再「都成立」 |
| `ov.rows` | {nand} NAND + {latch} LATCH · {rows} 行 | {nand} NAND + {latch} LATCH · {rows} rows | {rows} 行全部核过 · {nand} NAND + {latch} LATCH | {rows} rows, all checked · {nand} NAND + {latch} LATCH | 行数是人能理解的量，门数是工程量；把可理解的放前面 |
| `ov.none` | 尚未装电路 · 不设限 · 共享 halt 仍对它生效 | no circuit yet · not gated · the shared halt still applies | 还没有规则：这类工具现在**不设限**，「全部停下」也拦不住它——先给它定规则，或装上共享停机。 | No rules yet: this kind of tool is **not limited**, and Stop everything does not reach it — set its rules, or install the shared halt. | 实测（审计 F11）：没有共享停机电路时，全部停下之后这一类照样放行。原文「共享 halt 仍对它生效」只在装了 halt 时为真，且默认没装。「不设限」加重：`ov.none` 不在 `HTML_KEYS`，**采用加粗需把它加进去**，否则去掉星号 |
| `ov.halt.off` | 没有共享停机电路：blocked 只是电平，放下就恢复。 | No shared halt circuit: blocked is a level, lifted the moment it is lowered. | 没有装共享停机：「全部停下」只管得到装了规则的那几类，没规则的类拦不住。建议装上（模板「停机+心跳」）。 | No shared halt installed: Stop everything reaches only the kinds of tool that have rules; an unruled kind is not stopped. Installing it is recommended (template "halt + heartbeat"). | 同上；这行现在是总览里最容易被跳过的灰字，却是唯一说出这个缺口的地方 |
| `ap.stop.done` | 已封锁 {n} 个 agent：它们的下一次请求都会被拒。 | Blocked {n} agent(s): the next thing each one asks is refused. | 已停下 {n} 个 agent：装了规则的每一类都会拒绝它们的下一条请求。 | Stopped {n} agent(s): every kind of tool with rules refuses their next request. | 「都会被拒」在没装共享停机时对无规则类不成立（F11）；把范围说准 |
| `ov.install` | 去装一张 → | install one → | 给它定规则 → | Set its rules → | 「装一张」在没有「电路」上下文时不通 |
| `ov.pend.d` | 等一个人来点，模型点不了 | a person must press; the model cannot | 在等你点头，模型点不了 | waiting for your nod; the model cannot press | 与待确认页一致 |
| `ov.circ.ok` | 全部规则成立 · 全域已核 | every rule holds · every row checked | 每一行都核过 · 每条规则都成立 | every row checked · every rule holds | 与 `ov.holds` 同序 |
| `ev.title` | 当前规则的证据 | Evidence for the rules installed | 这组规则的证据 | Proof for these rules | 「证据 / proof」——它确实是穷举证明，不是抽样 |
| `ev.match` | 电路与参考实现逐行一致 | Circuit matches the reference on every row | 电路与规则原文逐行一致 | The circuit agrees with the rules on every row | 「参考实现」是开发者词；对人来说是「规则原文」 |
| `ev.holds` | 每条规则都成立 | Every rule holds | 每个可达状态里每条规则都成立 | Every rule holds in every reachable state | 把「可达状态」带进标题，正文里的数字才有落点 |
| `ev.holds.body` | 从复位出发穷尽每个可达状态下的每种输入：可达 <b>{reach} / {poss}</b> 个状态，配合独立计数器共 <b>{conf}</b> 个配置、<b>{proven}</b> 行，逐条规则计违规数。 | From reset, every input in every reachable state: <b>{reach} of {poss}</b> states reachable, <b>{conf}</b> configurations with independent monitors, <b>{proven}</b> rows, violations counted per rule. | 从开机状态出发，把 <b>{reach}</b> 个能到达的状态（共 {poss} 个可能）下的每一种输入都走了一遍，一共 <b>{proven}</b> 行，逐条规则数违规：全部为零。 | Starting from reset, every input was tried in each of the <b>{reach}</b> reachable states (of {poss} possible), <b>{proven}</b> rows in all, counting violations rule by rule: all zero. | 「独立计数器 / 配置」拿掉（{conf} 不再显示，仅出现在网表折叠里也可）；「全部为零」是结论，现在读者要自己去违规计数行找 |
| `honest.line` | 拍不是时间；标志位是工具层的承诺；链上只复算、不部署。 | Ticks are not time; flags are the tool layer's promise; on chain everything is replayed, nothing deployed. | 「拍」数的是调用次数，不是时间；哪些工具算不可逆是工具层说的；链上只复算、不部署。 | A tick counts calls, not time; which tools count as irreversible is the tool layer's word; on chain everything is replayed, nothing deployed. | 三句诚实话是好东西，但「拍不是时间」要说出「那是什么」 |
| `nav.foot` | 拍不是时间。\n标志位是工具层的承诺。\n链上只复算，不部署。 | Ticks are not time.\nFlags are the tool layer's promise.\nOn chain: replayed, never deployed. | 拍数的是调用，不是时间。\n不可逆由工具层判定。\n链上只复算，不部署。 | A tick is a call, not time.\nIrreversible is the tool layer's call.\nOn chain: replayed, never deployed. | 同上，保持三行 |

## 边界页（十条规则的名字）

| key | 现中文 | 现英文 | 提案中文 | 提案英文 | 为什么 |
| --- | --- | --- | --- | --- | --- |
| `b.blocks` | 电路积木 | Circuit blocks | 规则 | Rules | 这一栏就是规则表；「积木」是设计比喻，页面上没有搭积木的动作 |
| `b.blocks.sub` | 十条规则，各自落在哪个通道上 | ten rules, and which channel each rests on | 十条；蓝色标签的规则 agent 自己能付出代价绕开，橙色的它绕不开 | ten of them; a blue tag means the agent can pay its way past, an orange tag means it cannot | 「通道」没有解释；把「代价 / 边界」两色的含义写出来，标签才有意义 |
| `b.install` | 编译并安装 | Compile and install | 生效 | Apply | 人按下去想要的是「让规则生效」；「编译」是它内部做的事，成功消息 `b.installed` 已经把编译结果说全了 |
| `b.installed` | 已安装到 {cls}：{nand} NAND + {latch} LATCH，核对 {rows} 行，{holds}，用时 {ms} ms。 | Installed for {cls}: {nand} NAND + {latch} LATCH, {rows} rows checked, {holds}, in {ms} ms. | {cls} 的规则已生效：{rows} 行全部核过，{holds}（{nand} NAND + {latch} LATCH，{ms} ms）。 | Rules for {cls} are live: {rows} rows checked, {holds} ({nand} NAND + {latch} LATCH, {ms} ms). | 结论先行 |
| `b.denyall` | 整类拒绝（没有电路会放行） | deny the whole class (no circuit grants here) | 这类工具一律拒绝 | Refuse this kind of tool outright | 短、直接 |
| `b.denyall.hint` | 这是「永不」的唯一写法：上限填 0 表示不限，不是零次。 | The only way to say never: a limit of 0 means unlimited, not zero. | 要「永远不许」就勾这个。下面的数字里 0 表示「不限」，不是零次。 | Tick this to mean never. In the numbers below, 0 means "no limit", not zero times. | 先说动作，再说陷阱 |
| `r.gap` | 最小放行间隔（拍） | minimum gap (ticks) | 两次放行至少隔几次调用 | Calls between two grants, at least | 把「拍」翻成「次调用」，与 `honest.line` 一致；括号里的单位改进标题 |
| `r.commit` | 承诺拍数 | commitment (ticks) | 放行前要连续表态几次 | Consecutive intents before a grant | 同上 |
| `r.blocked` | blocked 为高时一律不放 | nothing while blocked | 停下时一律不放 | Nothing while stopped | 与「全部停下」一致；`blocked` 工程名已在旁边的 `code` 里 |
| `r.max` | 总放行上限 | total grants | 总共最多放行几次 | Total grants allowed | 微调 |
| `r.confirm` | 确认窗口（拍） | confirmation window (ticks) | 一次确认管几次调用 | Calls one confirm covers | 从人的角度说 |
| `r.sticky` | 封锁后停机，直到有人确认 | halt stays until a confirm | 停下后不自动恢复，要有人确认 | Stays stopped until a person confirms | 同上 |
| `r.heartbeat` | 连续 N 拍没有心跳就停机 | halt after N ticks without a heartbeat | 连续几次调用没有心跳就停下 | Stop after this many calls without a heartbeat | 同上 |
| `r.irrev` | 不可逆动作消耗一次确认 | an irreversible action spends its confirm | 收不回的动作每次都要人确认 | Anything that cannot be undone needs a confirm each time | 这是非开发者最该勾的一条，名字要一眼懂 |
| `r.trip` | 熔断：连续 N 次失败 | breaker: N consecutive failures | 连续失败几次就停下 | Stop after this many failures in a row | 「熔断」留在 hint 里 |
| `r.twokey` | 双钥：confirm 与 confirm_b 都要 | two keys: confirm and confirm_b | 两个人都点头才放 | Two people must both confirm | 同上 |
| `tp.fly` | 果蝇默认 | fly default | 果蝇反射弧（出处） | Fruit-fly reflex (the origin) | 说明这是产品来源的一组参数，不是推荐默认；不用「大脑」 |

## 接入 / 流水 / 其他

| key | 现中文 | 现英文 | 提案中文 | 提案英文 | 为什么 |
| --- | --- | --- | --- | --- | --- |
| `ac.tester.sub` | 演示用：这里两栏都是你在按，所以只是演示 | for demos: you press both channels here, so it is only a demonstration | 演示用：从这里发出的请求是你替 agent 按的，不算真边界 | For demos: a request sent from here is you pressing on the agent's behalf, so it proves nothing about the boundary | 「两栏」指代不明 |
| `e.why` | 拒绝原因（电路原话）： | refused because (the circuit's own words): | 为什么拒绝（反射弧原话）： | Why it was refused (the reflex arc's own words): | 保留「原话」；换比喻 |
| `tok.prompt` | 操作者令牌（安装规则、写 confirm/blocked 需要）。在后台启动日志里，也在 ~/.c3s-circuit-agent/operator-token。只存在这个浏览器里，不会发给服务器。 | Operator token (needed to install rules and write confirm/blocked). It is in the console's startup log and in ~/.c3s-circuit-agent/operator-token. Kept in this browser only; never sent to the server. | 这一步需要你的操作者令牌（确认、停下、改规则都要它，agent 拿不到）。它在后台启动时打印过，也在 ~/.c3s-circuit-agent/operator-token 里。粘贴一次，只存这个浏览器。 | This needs your operator token (confirming, stopping and changing rules all need it; the agent never has it). It was printed when the console started and is in ~/.c3s-circuit-agent/operator-token. Paste it once; it stays in this browser only. | 原文「不会发给服务器」不准确——它随每个写请求作为请求头发给本机后台；改成「只存这个浏览器」 |
| `models.none` | 还没有模型接入 | no model connected yet | 还没有模型接入 · 去「接入」 | no model connected yet · see Connect | 顶栏这个状态点是空状态第一眼看到的东西；给出口 |

## 新增键（配合审计里的标记改动；两种语言一并给出）

| key | 中文 | 英文 | 用在哪 |
| --- | --- | --- | --- |
| `first.title` | 三步接上你的第一个 agent | Three steps to your first agent | 总览空状态卡片标题（F7） |
| `first.lead` | 这是你 agent 的反射弧：你写规则，规则编成电路并逐行验证；agent 每一次调用先过它。你拿着确认和停下两把钥匙。 | This is your agent's reflex arc: you write rules, they compile to a circuit that is checked row by row, and every call the agent makes passes through it first. You hold the confirm and stop keys. | 同上，正文 |
| `first.s1` | 定规则 | Set the rules | 步骤 1 标题 |
| `first.s1.d` | 从模板挑一个：不能转钱、删文件要人确认、发消息要人确认。 | Pick a template: no money out, deletes need a person, messages need a person. | 步骤 1 说明 |
| `first.s2` | 接上你的模型 | Connect your model | 步骤 2 标题 |
| `first.s2.d` | Claude Code、任何 MCP 客户端，或 BNBAgent 钱包——选一个，复制一段配置。 | Claude Code, any MCP client, or the BNBAgent wallet — pick one and paste one block. | 步骤 2 说明 |
| `first.s3` | 看它拒绝一次 | Watch it refuse once | 步骤 3 标题 |
| `first.s3.d` | 在「接入」页发一条探测，第一眼就看见边界在。 | Send a probe from Connect and see the boundary answer. | 步骤 3 说明 |
| `first.go1` | 去定规则 | Set rules | 步骤 1 按钮 |
| `first.go2` | 去接入 | Connect | 步骤 2 按钮 |
| `first.go3` | 发一条探测 | Send a probe | 步骤 3 按钮 |
| `first.done` | 已完成 | done | 步骤完成态 |
| `ap.hint.once` | 每张卡右上的两位数是那条请求的号码；确认按钮上带同样的两位数，对得上，你批的就是它。 | The two digits on each card are that request's number; the confirm button carries the same two, so if they match you are approving that one. | 待确认列表顶部，只出现一次（F1） |
| `ap.stop.hold` | 按住不放 | Hold to stop | 按住过程中的按钮文字（F4） |
| `ap.stop.hint` | 按住半秒才会停下，防止误触。 | Hold for half a second; a tap alone does nothing. | 停下按钮下方一行小字（F4） |
| `ac.more` | 同一个 agent 连续 {n} 条同样的拒绝 · 展开 | {n} identical refusals from the same agent in a row · expand | 流水折叠行（F5） |
| `ap.halt.stale` | 停机已经解除；这条会在它下一次请求时从这里消失。 | The halt has been lifted; this card goes when the agent next sends. | 停机已复位但卡片仍在时（F6，需后台配合） |
