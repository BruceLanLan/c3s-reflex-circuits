# C3S Circuit Agent · 控制台 UI/UX 审计（2026-09-16 · Fable 5.1）

> 对象：`static/index.html`（六视图 + 今晚在建的 Tasks）。方法：起了一份私有后台（8776，
> `REFLEX_CONFIG_DIR=/tmp/ux-config`），装上四类规则 + 共享停机，灌入 5 个 agent、5 条待确认
> （含双钥付款、熔断、共享停机）、100 次请求（流水上限 60 条），在 1440 与 400px、中英、
> 深浅色下截图与量测；然后把 `optimized.css` 注入活页面复测。所有截图在
> `~/.playwright-mcp/ux/`（`empty-*` 空状态、`seed-*` 现状、`opt-*` 新样式·浅色、
> `optd-*` 新样式·深色、`stopped-*` 全部停下、`final-*` 末版）。
>
> 交付物：本文 + `optimized.css`（整块 `<style>` 的替换）+ `copy.md`（`t()` 键表）。
> `static/index.html` 今晚归 Tasks 视图的作者，本文只给 diff，不改文件。

## 结论先行

1. **待确认卡片读起来像表单**：备注输入框排在确认按钮之前，两位数离按钮 250px，
   人的解释比电路原话更暗（对比度 2.6:1，不达标）。→ CSS 已改（按钮先、备注后、
   两位数靠右、解释亮过原话、`--faint` 提到 4.5:1），文案见 copy.md。
2. **有一种拒绝是死路**：共享停机（无心跳）拒绝的卡片，原话说「a confirm lifts it」，
   页面却禁用按钮并说「先解除封锁」，而开关面板显示 blocked = 0。→ 后台 `pending[]`
   要给这类条目一个 `bit`（后台作者的活），页面侧文案先改。
3. **证据是页面上最小的那行字**：`18 NAND + 1 LATCH · 64 行` 12.5px 等宽灰字。→ 一句
   人话的证明行（`.proof`，需 3 行标记），门数退到括号里。
4. **停下按钮桌面版离语言切换 14px，一点即停**；手机版停靠底部很好找，但整宽 50px，
   滚动误触即停。→ 按住 600ms 触发（JS 约 20 行，CSS 已备 `.stopbtn.holding`），
   桌面加 12px 隔离；待确认页里「全部停下」卡片改到列表下方，只在停下生效时回到顶部（CSS `:has`）。
5. **首次打开什么都没教**：四个 0、一张默认电路、一颗红按钮、六个位名。→ 总览加一张
   「三步」卡片（标记 + 13 个键），按状态勾掉已完成的步骤。
6. **手机导航只有图标**：7 个 36×44 的图标，非开发者认不出。→ 图标下加短标签（CSS 已改），
   中文导航键改为纯中文（copy.md），否则 `Overview · 总览` 只能省略成 `Overvie…`。
7. **「全部停下」在默认安装上停不住没规则的那几类** [验证]：没有共享停机电路时，stop-all 之后
   `message` 类（未装规则）的请求照样 `granted: true`（`blocked: 1` 在输入里，但没有电路读它）。
   页面上「已封锁 n 个 agent：它们的下一次请求都会被拒」此刻是假话。→ 文案改准（copy.md
   `ov.none` / `ov.halt.off` / `ap.stop.done`）；产品上建议默认装共享停机——那是 console 作者的活（F11）。

已验证 / 待怀疑分开标注：**[验证]** = 截图或量测；**[怀疑]** = 我的判断，没有用户测过。

---

## F1 · 一个人能不能不懂系统就批对那一条？—— 卡片是表单的形状

**人做什么**：手机上打开待确认页，想批准「把钓鱼邮件扔垃圾箱」。

**哪里出问题**  [验证]
- 现状卡片顺序：效果框 → agent / 类别 / tick / 匹配码 → 原始 `reason` → 电路原话 →
  人的解释 → 匹配码提示 → **备注输入框 → 确认按钮 → 封锁按钮**。手机上第一张卡 493px 高，
  匹配码在 y=654、确认按钮在 y=903，中间隔一个输入框（`seed-approvals-phone-zh.png`，
  量测：`.code` top 654 / `.go` top 903 / `.notein` top 851）。人读到输入框会先想「要填什么」。
- 每张卡都重复一遍两位数的解释（`ap.code.hint`，两行），五张卡五遍。
- 人的解释 `.note` 是 `#5E646C` 12px，电路原话 `.why li` 是 `#ECE7DC` 12px 等宽：机器句子
  比人的句子亮。`#5E646C` 在 `#202529` 上对比度 **2.59:1**（WCAG AA 要 4.5）。
- 匹配码在 `.who` 行里跟着 tick 和时间走，不在固定位置。

**改什么**
- CSS（已在 `optimized.css`）：`.pend .btns` 内用 `order` 把确认按钮排第一、封锁第二、
  备注第三，备注框降到 40px/13px；`.codeline { margin-left:auto }` 让两位数贴卡片右缘，
  `.code` 20px（手机 24px）；`.pend .why + .note` 升到 `--text` 13px，`.why li` 降到 `--dim`；
  `--faint` 从 `#5E646C` 提到 `#858C95`（4.55:1）。复测：手机上确认按钮 y=648，两位数
  y=465，中间只有原话与解释（`final-approvals-phone-zh-viewport.png`）。
  深色调色板离开画板的令牌有两个：`--faint`（上面的理由）和 `--dim`（`#979CA3` → `#A2A7AE`，
  5.6 → 6.4:1，因为它现在承担原话与备注两层次要文字）；半透明叠色 `--dim-rgb` 仍用画板值
  `151,156,163`，只影响徽章底色。其余颜色与画板一致。
- 文案（copy.md）：`ap.confirm` → 「确认这一条 · 75」，`ap.pending` → 「等你点头」，
  `ap.code` → 「核对码」，`ap.code.hint` 只在列表顶部说一次（新键 `ap.hint.once`）。
- 标记 diff（列表顶部说一次，卡片里不再重复）：

```diff
-      <div class="card"><div class="h2"><span data-i18n="ap.pending"></span><span class="sub" id="ap-pending-sub"></span></div><div id="ap-list" style="display:grid;gap:10px"></div></div>
+      <div class="card"><div class="h2"><span data-i18n="ap.pending"></span><span class="sub" id="ap-pending-sub"></span></div><div class="hint-once" data-i18n="ap.hint.once"></div><div id="ap-list" style="display:grid;gap:10px"></div></div>
```
```diff
 renderApprovals · item():
-      ${wait || !p.code ? "" : `<div class="note">${t("ap.code.hint")}</div>`}</div><div class="btns">${btns}</div></div>`;
+      </div><div class="btns">${btns}</div></div>`;
```
  （`.hint-once` 在没有待确认时也会显示；如不想要，在 `renderApprovals` 里
  `document.querySelector(".hint-once").hidden = !pending.length;`。）

**怎么知道**：`seed-approvals-phone-zh.png` vs `final-approvals-phone-zh-viewport.png`；
对比度用 WCAG 相对亮度公式算（脚本在会话里，数值：faint 2.59 → 4.55；dim 5.6；text 12.5）。

## F2 · 拒绝读得懂吗？—— 原话之上要有一句人话，而且要说下一步

**人做什么**：看到一条被拒，想知道「我该做什么」。

**哪里出问题**  [验证]
- 原话是英文等宽（`irreversible, and no unspent confirm`），中文界面也是英文——这是产品的
  诚实，不改。但它上面/下面那句人话没有告诉人下一步：`ap.irrev.note` 只教怎么批，没说
  「不批也可以」；`ap.break.note`「熔断已跳。一次 confirm 复位」没说去哪看失败了什么。
- 双钥卡（`studio:my-agent` 付款 0.25 BNB）：`why` 有两行，`itemNote()` 只取第一个匹配
  （`/^irreversible/`），解释写的是不可逆，**没提第二把钥匙**；按钮写「确认一次」。人按下后
  这条继续等 `confirm_b`，页面没解释为什么（`seed-approvals-desk-zh.png` 第二张卡）。

**改什么**
- 文案：copy.md 里 `ap.irrev.note` / `ap.break.note` / `ap.halt.note` / `ap.wait.note` /
  `ap.key.note` / `ap.blocked.note` 全部改成「后果 + 下一步」句式。
- JS diff（双钥优先于不可逆，因为它是人当下最需要知道的）：

```diff
 const NOTE_FOR = [
   [/^two keys needed/, "ap.key.note"],
   [/^irreversible/, "ap.irrev.note"],
 ...
 function itemNote(p) {
   const why = p.why || [];
   if (why.some((w) => String(w).startsWith("blocked is high"))) return "ap.blocked.note";
-  for (const w of why) for (const [re, key] of NOTE_FOR) if (re.test(w)) return key;
+  for (const [re, key] of NOTE_FOR) if (why.some((w) => re.test(String(w)))) return key;  // rule order wins, not line order
   return p.bit && !p.waiting_on_time ? "ap.needs.note" : "ap.wait.note";
 }
```
- CSS（已改）：解释句 `--text` 13px，原话 `--dim` 12px 等宽——原话仍在，作为引文。

**怎么知道**：`seed-approvals-desk-zh.png`（第二张卡：两行原话、一句只讲不可逆的解释）。

## F3 · 证据落地了吗？—— 最该让人安心的一行是最小的字

**人做什么**：想知道「这规则真的管用吗」。

**哪里出问题**  [验证]
- 总览四张小卡里 `108 NAND + 9 LATCH · 32,768 行` 是 12.5px 等宽，`✓ 每条规则成立 · 全域已核`
  12px（`seed-overview-desk-zh.png`）。门数排在行数前面；门数对人没有意义，行数有。
- 边界页证据栏是工程报告的形状：两条 verdict 行 + 五个 tile（NAND / LATCH / 字节 / 深度 /
  ms）+ 违规计数一行 + 网表折叠（`seed-boundaries-spend-desk-en.png`）。结论「违规全部为零」
  要人自己去那一行数 0。「配合独立计数器共 624 个配置」是评审词。

**改什么**
- CSS（已改）：`.mini .st` 13px/500；`.verdict-row .head` 14px/600、mark 17px；tile 数字降到
  15px（它们是次要量）；新增 `.proof`——一句话证明行，绿底细框，数字等宽。
- 文案：`ov.rows` 行数在前；`ov.holds` / `ov.circ.ok` 改为「每一行都核过，每条规则都成立」；
  `ev.holds.body` 以「全部为零」收尾（copy.md）。
- 标记 diff（边界页证据栏顶部加一句证明；`renderBoundaries` 里 `ev.innerHTML` 开头）：

```diff
   ev.innerHTML = `<div style="display:grid;gap:10px">
+    <div class="proof${k.matches_reference && k.every_rule_holds ? "" : " bad"}"><span class="mark">${k.matches_reference && k.every_rule_holds ? "✓" : "✗"}</span><span>${t("ev.proof", { rows: num(k.rows), reach: num(k.reachable_states) })}<small>${t("ev.proof.sub", { nand: num(c.nand), latch: num(c.latch), ms: num(p.compiled_in_ms) })}</small></span></div>
     <ul class="words">...
```
  新键：`ev.proof` zh「**{rows}** 行全部核过；**{reach}** 个可达状态里每条规则都成立。」
  en「All **{rows}** rows checked; every rule holds in each of the **{reach}** reachable states.」
  `ev.proof.sub` zh「{nand} NAND + {latch} LATCH · 编译加检查 {ms} ms」en「{nand} NAND + {latch}
  LATCH · compiled and checked in {ms} ms」。两键含 `<b>`，要加进 `HTML_KEYS`。
- [怀疑] 总览小卡的 `.ct` 行也可以直接用 `ev.proof` 的短版；没做，因为四张卡一起变会比
  现在的画板重。留给用户看了 `.proof` 之后再决定。

## F4 · 停下按钮：慌乱时找得到，误触时按不下

**人做什么**：出事了，抓手机按停；或者只是滑动页面。

**哪里出问题**  [验证]
- 桌面：`.bar .stopbtn` 32px 高的红色胶囊，和时钟、语言切换在同一行，间距 14px
  （`seed-overview-desk-zh.png` 右上）。一次 click 立刻 `POST /api/stop-all`，没有任何缓冲。
- 手机：`.stopdock` 固定在底部，50px 高、整宽（好找，这是对的）；但滚动时手指落在上面就是
  一次 click。Playwright 里 `page.click('#stop-all-2')` 一下就停了 5 个 agent
  （`stopped-approvals-phone-zh-viewport.png`）。
- 待确认页里「全部停下」卡片是左列第一张，正常状态也占 100px+，把第一张待确认卡推到
  y=519（手机）。停下之后它才有内容（横幅、解除）。
- 恢复流程本身是好的：点「解除全部封锁」→ 输入「解除」→ 确认（验证：输错不亮，输对后
  `resume-go.disabled=false`，`/api/resume-all` 成功，5 个 agent blocked 归 0）。

**改什么**
- 交互：**按住 600ms 触发**（不是确认对话框——对话框在慌乱时是障碍；按住是触屏紧急操作
  的通行做法，且滑动不会触发）。CSS 已备：`.stopbtn.holding::before` 从左到右 0.6s 填充，
  `prefers-reduced-motion` 下瞬时。JS diff：

```diff
-["stop-all", "stop-all-2", "stop-big"].forEach((id) => {
-  const b = $(id);
-  if (b) b.addEventListener("click", () => { show("approvals"); stopAll(true); });  // the banner and the way back are there
-});
+// Hold to stop: a press that lasts 600 ms fires; lifting the finger earlier cancels. A tap alone
+// (a scroll that lands on the dock, a click meant for the language toggle) does nothing.
+const HOLD_MS = 600;
+["stop-all", "stop-all-2", "stop-big"].forEach((id) => {
+  const b = $(id);
+  if (!b) return;
+  let timer = null;
+  const start = (ev) => {
+    if (b.disabled || timer) return;
+    if (ev.type === "pointerdown" && ev.button !== 0) return;
+    b.classList.add("holding");
+    timer = setTimeout(() => { timer = null; b.classList.remove("holding"); show("approvals"); stopAll(true); }, HOLD_MS);
+  };
+  const cancel = () => { if (timer) clearTimeout(timer); timer = null; b.classList.remove("holding"); };
+  b.addEventListener("pointerdown", start);
+  ["pointerup", "pointerleave", "pointercancel"].forEach((e) => b.addEventListener(e, cancel));
+  b.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); start(ev); } });
+  b.addEventListener("keyup", cancel);
+  b.addEventListener("click", (ev) => ev.preventDefault());  // the hold is the gesture; a click is not
+});
```
  按钮下加一行 `ap.stop.hint`「按住半秒才会停下，防止误触。」（手机上放在 `.stopdock` 里，
  桌面放 `title` 属性即可）。
- 布局（CSS 已改）：桌面 `.bar .stopbtn` 左右各 8/12px 外边距；待确认页三张卡用 `order`
  改为「待确认 → 已处理 → 全部停下」，`.card:has(.stopbanner:not([hidden]))` 在停下生效时把
  停下卡送回顶部——验证：停下后 order 0、y=282；恢复后 order 2（Playwright 量测）。手机上
  卡片里的红按钮隐藏（底部停靠已经是它），横幅、演练、解除保留
  （`optd-stopped-phone-en-viewport.png`）。
- [怀疑] `:has()` 需要 Safari 15.4+ / Chrome 105+。老浏览器只是不重排，不坏。

## F5 · 一百条流水：同一个 agent 的同一句拒绝重复 60 次

**人做什么**：打开流水想找「刚才为什么被拒」。

**哪里出问题**  [验证]
- 100 次请求后流水 60 条（后台上限），页面 9154px 高；`bulk:load` 的 60 条一模一样
  （`halted (shared halt circuit)…; blocked is high`），每条 4 行（`seed-activity-desk-zh.png`）。
  总览「最近决策」也被同一个 agent 占满。

**改什么**
- CSS（已改）：`.e` 内边距 8→7px，`.e.more` 折叠行样式（点线左边、计数徽章）。
- JS（分组是逻辑，不是样式）：`renderActivity` 里把**连续、同 agent、同 class、同 granted、
  同 why** 的请求条目折成一行，显示第一条 + `ac.more`「同一个 agent 连续 {n} 条同样的拒绝 ·
  展开」，点开展开全部。约 15 行。总览 `slice(0, 6)` 改成折叠后再取 6。
- [怀疑] 也许更好的是后台就在 `transcript` 里带 `repeat` 计数；那是 API 变更，今晚不提。

## F6 · 一种拒绝是死路：共享停机

**人做什么**：看到 `bulk:load` 那张卡，想让它恢复。

**哪里出问题**  [验证]
- 卡片原话：`halted (shared halt circuit): halted until a confirm lifts it; blocked is high`。
  `pending[]` 里这条 `bit: null, waiting_on_time: true, code: null` → 页面禁用两个按钮，
  `itemNote()` 因为 `blocked is high` 匹配到 `ap.blocked.note`：「这个 agent 正被封锁…先解除
  封锁（右边开关面板）」。但 `agents[].armed.blocked = 0`，开关面板显示「拉高」——没有东西可
  解除（`seed-approvals-phone-en.png` 最后一张卡）。「right」在手机上也没有右边。
- `POST /api/resume-all` 后停机计数器归零（验证：`by_class.halt.counters.halted = 0`），
  但这张卡留到 agent 下一次请求才消失，页面无从得知。

**改什么**
- 后台（不是我的文件；请 console 作者看）：共享停机的拒绝应给 `bit: "confirm"`（API.md 写
  「halted」属于人能解的一类）或至少 `waiting_on_time: false` 并带 `code`；`blocked is high`
  在这里是停机电路的输出，不是人写的位，`why` 里最好区分（例如 `blocked by the shared halt`）。
- 页面侧先做：`itemNote()` 里 `halted (shared halt circuit)` 的匹配放在 `blocked is high` 之前，
  用 `ap.halt.note`（copy.md 已改成说「被封锁过，或太久没有心跳；点确认解除；这条要它再发」）。
  `ap.blocked.note` 去掉「右边开关面板」。

```diff
 function itemNote(p) {
   const why = p.why || [];
+  if (why.some((w) => String(w).startsWith("halted (shared halt circuit)"))) return "ap.halt.note";
   if (why.some((w) => String(w).startsWith("blocked is high"))) return "ap.blocked.note";
```

## F7 · 首次打开：空状态什么都没教

**人做什么**：刚装好，打开 `http://127.0.0.1:8765`。

**哪里出问题**  [验证]
- 总览：`已接入模型 0 / 电路 1 张 / 最近决策 0 / 待确认 0`，四张小卡三张写「尚未装电路 · 不设限 ·
  共享 halt 仍对它生效」，右侧「还没有记录」，右上一颗红色「全部停下」（`empty-overview-desk-zh.png`）。
  没有一处说「接下来做什么」。手机上四个 0 竖排占 480px（`empty-overview-phone-zh.png`）。
- 待确认空状态：第一张卡是红按钮 + 演练；右栏六个位名 `blocked confirm irreversible failed
  heartbeat confirm_b`，下拉框写「还没有 agent。」（`empty-approvals-desk-zh.png`）。
- 顶栏「还没有模型接入」是灰点，不是链接。

**改什么**
- 标记：总览 `<section data-view="overview">` 开头加一张卡（CSS `.first` 已备）：

```diff
 <section data-view="overview">
+  <div class="card first" id="ov-first" hidden>
+    <div class="h2" data-i18n="first.title"></div>
+    <p data-i18n="first.lead"></p>
+    <div class="first-step" data-step="rules"><span class="n"><span>1</span></span><div><b data-i18n="first.s1"></b><small data-i18n="first.s1.d"></small></div><button class="go tool" type="button" data-go="boundaries" data-i18n="first.go1"></button></div>
+    <div class="first-step" data-step="connect"><span class="n"><span>2</span></span><div><b data-i18n="first.s2"></b><small data-i18n="first.s2.d"></small></div><button class="pre" type="button" data-go="connect" data-i18n="first.go2"></button></div>
+    <div class="first-step" data-step="probe"><span class="n"><span>3</span></span><div><b data-i18n="first.s3"></b><small data-i18n="first.s3.d"></small></div><button class="pre" type="button" data-go="connect" data-i18n="first.go3"></button></div>
+  </div>
   <div class="grid4" id="ov-stats"></div>
```
  JS（`renderOverview` 末尾）：

```diff
+  // First run: the three steps, with the ones already done ticked. Hidden once all three are.
+  const rulesDone = TOOL_CLASSES.some((c) => { const p = policyOf(c); return p && (p.deny_all || c !== "exec" || JSON.stringify(p.rules) !== JSON.stringify(s.default_rules || [])); });
+  const connectDone = models.some((m) => m !== "console" && m !== "probe");
+  const probeDone = s.transcript.some((e) => e.kind === "request" && !e.granted);
+  const first = $("ov-first");
+  first.hidden = rulesDone && connectDone && probeDone;
+  [["rules", rulesDone], ["connect", connectDone], ["probe", probeDone]].forEach(([k, d]) => first.querySelector(`[data-step="${k}"]`).classList.toggle("done", d));
+  first.querySelectorAll("[data-go]").forEach((b) => { b.onclick = () => show(b.dataset.go); });
```
  （`s.default_rules` 是默认 exec 电路的规则句子列表，与 `p.rules` 同形，验证过；判据 =
  「除 exec 之外任一类装了规则，或 exec 不再是默认那组」。）
- 待确认空状态：右栏卡加 `agentless` 类（`.agentless #ap-toggles, .agentless #ap-agent`
  已在 CSS 里隐藏），`ap.noagent` 改成带出口的句子（copy.md）。JS：
  `$("ap-agent").closest(".card").classList.toggle("agentless", !names.length);`
- 手机总览：`.grid4` 两列（CSS 已改，`optd-overview-phone-zh-viewport.png`）。
- 顶栏 `models.none` 加「去接入」字样（copy.md）；[怀疑] 让它可点需要一个 `data-go`，两行 JS。

## F8 · 手机导航只有图标；顶栏三行

**哪里出问题**  [验证]
- ≤900px 时 `.nav-item span.lab { display:none }`，7 个 36×44 图标（`seed-approvals-phone-zh-viewport.png`）。
  盾、收件箱、心电、齿轮——非开发者不会把「盾」读成「边界」。
- 顶栏 116px：标题 + 三个状态点 + 时钟 + 语言切换换了三行。

**改什么**（CSS 已改，量测在括号里）
- 图标上、标签下，`flex:1 1 0`，标签 10.5px 单行省略（导航 88px，每项 53×44）。
  中文导航键改纯中文（copy.md），否则省略成 `Overvie…`（`opt-approvals-phone-zh-viewport.png`
  是改 CSS 未改文案的样子）。
- 顶栏：时钟隐藏（手机有钟）、语言切换绝对定位到标题行右侧、状态点一行 →
  待确认页 73px、边界页 94px（副标题较长）。`scrollWidth` 全程 = 400。

## F9 · 浅色主题不存在

**哪里出问题**  [验证]
- 现状 `html { color-scheme: dark }`，所有颜色写死。系统浅色的用户拿到深页面；
  `input` 背景 `#14171a`、表格线 `#23282e` 是字面量。

**改什么**（CSS 已改）
- 令牌化 + 三态：`:root` = 画板深色（默认）；`@media (prefers-color-scheme: light)` 下
  `:root:not([data-theme="dark"])` 浅色；`:root[data-theme="light"]` 强制浅、
  `:root[data-theme="dark"]` 强制深。半透明叠色全部改成 `rgba(var(--x-rgb), a)`。
- **这是一个决定，不是细节**：任务书写的是「`:root` 浅、`prefers-color-scheme: dark` 深」
  （Artifact 惯例）；我选了**深色优先**，因为画板是深色、页面今天就是深色，浅色优先会让
  系统浅色的用户在 CSS 落地那一刻换脸。两个调色块互换即可反过来。请协调者拍板。
- 验证四态：系统深 → `--bg #15181C`；系统深 + `data-theme=light` → `#F3F1EB`；系统浅 →
  `#F3F1EB`（`opt-*.png` 全组）；系统浅 + `data-theme=dark` → `#15181C`。浅色截图：
  `opt-approvals-desk-zh.png`、`opt-boundaries-desk-zh.png`、`opt-connect-desk-zh-light.png`。
- 页面没有主题切换按钮；[怀疑] 不必加，跟系统走即可。

## F11 · 「全部停下」停不住没规则的类（默认安装就是这个状态）

**人做什么**：出事了，按「全部停下」，以为一切都停了。

**哪里出问题**  [验证，8776 实测]
- 移除 `halt` 与 `message` 两张电路（默认安装本来就没有这两张），`POST /api/stop-all` 成功
  （`stopped: true, count: 5`），随后 `tg:12345` 发一条 `message` 类请求 →
  `{"granted": true, "class_installed": false, "why": [], "inputs": {"blocked": 1, …}}`。
  blocked 位写进去了，但没有任何电路读它。装回 `halt` 后同一请求 →
  `granted: false, why: ["halted (shared halt circuit): blocked is high; …"]`。
- 页面此刻显示 `ap.stop.done`「已封锁 5 个 agent：它们的下一次请求都会被拒」、横幅
  `ap.stopped.banner`「…下一次请求都会被拒」。对无规则的类，这两句是假的。总览里唯一说出
  这个缺口的是灰字 `ov.halt.off`「没有共享停机电路：blocked 只是电平，放下就恢复」——
  这句话说的是电平语义，不是「停不住」。

**改什么**
- 文案（copy.md）：`ov.none` 明说「全部停下也拦不住它」；`ov.halt.off` 明说停下的范围并建议装
  共享停机；`ap.stop.done` 把范围说成「装了规则的每一类」。
- 页面（JS，小）：`renderStop()` 里若 `!isLive(policyOf(HALT))` 且存在未装规则的类，在停下卡
  横幅下加一行 `.note.bad`：「{classes} 没有规则也没有共享停机：这次停下管不到它们。」
  新键 `ap.stop.gap`（zh 同上；en "{classes} have no rules and there is no shared halt: this
  stop does not reach them."）。
- 产品（console.py，非本人文件，最重要的一条）：**默认安装共享停机电路**（`sticky_block` +
  `forbid_when_blocked`，心跳可不开），或让 stop-all 在没有 halt 电路时把所有未装规则的类当成
  整类拒绝直到恢复。一个「全部停下」按钮存在的前提是它真的全部停下。

## F10 · 小项（都已验证，改动小）

- **Connect 里第二个 `<style>`**（`.picks .pick .cnrow #cn-tools .srcpat`）已并入
  `optimized.css`；落地时删掉 `<section data-view="connect">` 里那段 `<style>`。
- **Tasks 视图**的 `.task .tid .task-e` 规则今晚也在 `index.html` 里长出来了；`optimized.css`
  里放了令牌化的一份（把 `rgba(111,168,220,…)` 换成 `rgba(var(--agent-rgb),…)`），落地时以
  Tasks 作者的为准合并。另：现在导航里 `nav.tasks` 显示为原始键名——那是他们的半成品，
  不是本审计的发现。
- `tok.prompt` 说令牌「不会发给服务器」不准确：它作为 `x-reflex-token` 头随每个写请求发给
  本机后台。copy.md 改为「只存这个浏览器」。
- `b.install`「编译并安装」→「生效」；编译细节在成功消息里已经说全。
- 表格 `td.name` 22ch 上限在 400px 下与 `.wrap` 横向滚动配合正常；未见断行问题。
- `.nav-item .cnt` 徽章在手机上改为绝对定位到图标右上，不再挤掉标签。

## 决定不做的

- **不给确认加对话框**：两位数 + 单击已经是「读到才批得对」的机制；再加一层是把慌乱时的
  停下和平时的批准一起变慢。停下改按住，批准不动。
- **不重排边界页十条规则**：它是开发者页面，密度是它的价值；只改名字（copy.md）和证明行。
- **不把两位数移进效果框**（`.eff .top` 右侧）：要动 `item()` 的模板，且现在贴右缘已经能
  在同一视线上读到。CSS 里留了 `.eff .code-top` 备用。
- **不改 `ap.resume.word`**（用户要打的字「解除 / RESUME」）：改它改的是手势。
- **不动 `ruleWordsZh()` 里的中文**：它是 JS，不是 `t()` 键；虽有「拍」字，留给下一轮。
- **不加主题切换按钮**：跟系统走。
- **不出设计画板 / Artifact**：把 `optimized.css` 注入活页面截出来的图比手画的稿更真——它就是
  落地后的样子，带真实数据。画板留给需要用户在几种方向里挑的时候；这一轮方向没变，只是修。

## 需要落地的标记 / JS 改动一览（按价值排）

| # | 改动 | 类型 | 对应 |
| --- | --- | --- | --- |
| 1 | 整块 `<style>` 换成 `optimized.css`；删 Connect 内嵌 `<style>` | 替换 | 全部 |
| 2 | `copy.md` 里的键值（中英同步）；新键 `first.*` `ap.hint.once` `ac.more` `ap.halt.stale` `ev.proof` `ev.proof.sub` `ap.stop.gap`。**`ap.stop.sub` / `ap.stop.hold` / `ap.stop.hint` 三键要和第 3 行一起落**，否则文案在描述一个还不存在的手势 | 字典 | F1–F7 F11 |
| 3 | 停下按住触发（JS 约 20 行） | JS | F4 |
| 3b | 停下卡在没有共享停机且有类未装规则时加一行范围提示 | JS ~5 行 + 键 `ap.stop.gap` | F11 |
| 4 | `itemNote()` 规则顺序优先 + 共享停机优先 | JS 2 行 | F2 F6 |
| 5 | 待确认列表顶部 `.hint-once`，卡片内去掉重复提示 | 标记 + JS 1 行 | F1 |
| 6 | 总览 `#ov-first` 三步卡 + 渲染逻辑 | 标记 + JS ~10 行 | F7 |
| 7 | 边界页 `.proof` 证明行 | JS 1 行 + 2 键进 `HTML_KEYS` | F3 |
| 8 | 流水连续重复折叠 | JS ~15 行 | F5 |
| 9 | 开关卡 `agentless` 类 | JS 1 行 | F7 |
| — | 后台：共享停机的 `pending[]` 条目给 `bit`/`code`；`why` 区分停机输出与人写的 blocked | console.py（非本人） | F6 |
| — | 后台：默认装共享停机电路，让「全部停下」名副其实 | console.py（非本人） | F11 |

## 截图索引（请用户看这几张）

| 看什么 | 现状 | 改后 |
| --- | --- | --- |
| 手机待确认第一屏 | `seed-approvals-phone-zh-viewport.png` | `final-approvals-phone-zh-viewport.png` |
| 手机待确认整页（5 张卡） | `seed-approvals-phone-zh.png` | `optd-approvals-phone-zh.png` |
| 桌面待确认 | `seed-approvals-desk-zh.png` | `optd-approvals-desk-zh.png`（深）· `opt-approvals-desk-zh.png`（浅） |
| 全部停下之后（手机） | `stopped-approvals-phone-zh-viewport.png` | `optd-stopped-phone-en-viewport.png` |
| 空状态总览 | `empty-overview-desk-zh.png` · `empty-overview-phone-zh.png` | （三步卡需落标记后才能截） |
| 边界页证据 | `seed-boundaries-spend-desk-en.png` | `optd-boundaries-desk-zh.png` · `opt-boundaries-desk-zh.png`（浅） |
| 一百条流水 | `seed-activity-desk-zh.png` | （折叠需 JS） |
| 手机总览 | `seed-overview-phone-zh.png` | `optd-overview-phone-zh-viewport.png` |
