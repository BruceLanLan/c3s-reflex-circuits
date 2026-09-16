# C3S Circuit Agent 指南

这一份同时写给两种人。**第一部分**给要用它的人：装上、写下边界、第一次批准、出事了怎么办。
**第二部分**给要改它、接它、审它的人：架构、语义、接口、怎么加一条规则、怎么写一个适配器。

中间那条横线是分界。只想用的人可以在那里停下；只想改的人可以从那里开始。

---

# 第一部分 · 给用的人

## 1. 五分钟装好

需要：macOS 或 Linux、`python3` 是 Python 3.10 以上、一个终端。不需要钱包、不需要账号；只在拉
Python 包时上一次网。

两个仓库。电路仓是编译器和它的证明，后台在它上面跑：

```sh
git clone https://github.com/BruceLanLan/c3s-reflex-circuits ~/work/c3s-reflex
git clone <后台仓库> reflex-console && cd reflex-console    # 尚未公开发布；从交给你的地方克隆
sh install.sh
```

`install.sh` 把 `c3s` 命令装进一个隔离环境（优先 `uv`，其次 `pipx`，都没有就在
`~/.c3s-circuit-agent/venv` 建一个普通 venv），记住这个 checkout，然后起后台、开页面。**不碰系统
Python，不用 sudo。** 电路仓不在 `~/work/c3s-reflex` 就先 `export C3S_REPO=<路径>`；装完提示找不到
`c3s`，是 `~/.local/bin` 不在 `PATH` 里。

装完你会看到一串**操作者令牌**。它是"人"这个角色的钥匙——后面所有要人点头的动作都要它。它同时
存在 `~/.c3s-circuit-agent/operator-token`，权限 600。

```sh
c3s status        # 在不在跑、几件事在等人、日志在哪
c3s token         # 再看一次令牌；c3s token rotate 换一个（换完页面、手机、bot 都要重新给）
c3s down          # 停掉
c3s --port 8766 up   # 换端口（--port 在子命令前面）
```

想让它开机自启：`c3s up --service`，装一个 launchd agent，被杀了也会拉回来，日志在
`~/.c3s-circuit-agent/console.log`。只做了 macOS。

## 2. 先看它跑一遍

```sh
c3s demo
```

一个假的邮箱、日历、文件夹，一件差事，每次调用都过电路。没有账号、没有密钥，什么都不会离开这台
机器。它装的规则是"发出去、删掉、覆盖都要人点头"，然后一个假 agent 照着一封钓鱼邮件的要求去删
文件、往外发客户名单——两次都被拒；它该发的那封回信也被拒，直到你在页面上批准；批准之后它再去
删文件，还是被拒，因为那次批准只属于那封回信。最后一节是账：`sent 1`、`trashed nothing`。

`c3s demo --headless` 让脚本自己按下人的那个键（要这个 shell 里有操作者令牌），录屏和测试用；
`c3s demo --with-claude` 把差事交给一个真的 `claude -p`，走同一份 `mcp.json`。

## 3. 你的第一条边界

打开页面的**边界（Boundaries）**。工具分四类，外加一颗所有类共享的停机电路：

| 类 | 管什么 | 建议 |
| --- | --- | --- |
| `spend` | 花钱、签名、转账 | **整类拒绝** |
| `message` | 发出去的消息 | 不可逆的要人点头 |
| `files` | 写、删、覆盖 | 不可逆的要人点头 |
| `exec` | 跑命令；分不进别的类的调用都落这里 | 连续失败就跳闸 |
| `halt` | 共享停机 | 一拍 blocked 就全停，直到人解除；n 拍没心跳也全停 |

挑一个模板，点编译。**先看证据面板再点安装。** 它会告诉你：

* **多少个 NAND 门、几个锁存器**——这颗电路有多大。
* **核对了多少行**——这是它输入定义域的全部行数，不是抽样。
* **多少个可达状态、证明了多少行**——每条规则在每个到得了的状态里都验过。
* **每条规则是否都成立**——这一行是 `true` 才安装。

两件事要知道：

* **`exec` 一开始就有一颗电路**，是果蝇反射弧的默认规则：两次放行至少隔 8 拍，放行前要连续 4 拍
  intent。这是给电路做演示用的，不是给你的 agent 干活用的——接上 Claude Code 之后第一批命令会被
  "cooldown: 7 ticks left of 8" 拒掉，就是它。给 `exec` 装一个你自己的模板（比如"连续失败三次跳闸"）
  把它换掉。
* 如果某件事你的答案是"绝对不行"，不要去写一个很大的数字：`max_grants=0` 是**不限次**，不是零次。
  选**整类拒绝**，编译出来的电路里没有任何通往放行的路径。

## 4. 第一次批准，以及那两位数字

接上模型之后（下一节），它迟早会撞到边界。**待确认（Approvals）**页会出现一条，长这样：

```
claude-code:mine · files · 第 1 拍                              匹配码 71
[files] Bash [irreversible]: rm -rf ./build
拒绝原因（电路原话）：irreversible, and no unspent confirm
[ 批准这一次 · 71 ]  [ 拦下这个 agent ]
```

那个 **71** 是这一次调用的两位数。页面上的按钮自带它，不用打；从 Telegram 或微信批的时候要把它
打出来。它不是密码——泄露了也没用——它的作用是**逼着批准的人看到这次调用本身**，而不只是"在
白名单里"：

* 一次批准只对**这一次调用**有效，按调用内容绑定（`for_reason`）。
* 用掉就没了，不会变成长期通行证。
* 挪不到别的调用上——哪怕那个 agent 紧接着换了个更危险的请求。

早期版本没有这条，代价是真实的：一次给"回邮件"的批准，被下一个"删邮件"的调用花掉了。现在不会了，
而且有测试锁着。

**模型改了措辞怎么办？** 模型重试时经常换一次说法（`rm -rf ./build` 变成 `rm -rf build/`），于是那次
批准对不上它了。这时拒绝回复里多一个字段 `confirm_waiting_for`，写着正在等的是哪一次调用；Claude Code
钩子和 MCP 代理都把这句话转给模型，让它把原话重发一遍即可。

## 5. 把你的模型接进来

页面的**接入（Connect）**有三条路，选你在用的那条。

### Claude Code

把 `adapters/claude_code_hook.py` 配成 PreToolUse 钩子，`claude_code_post_hook.py` 配成 PostToolUse 和
PostToolUseFailure（后两者让"失败"能进电路，跳闸规则才有意义）。Bash、Write、Edit 和所有 MCP 工具
都会经过它。接入页能替你把这两段写进 `~/.claude/settings.json`（先备份，原文件解析不了就拒绝动它）；
`adapters/README.md` 有手写的版本。**把钩子配置放在 agent 改不了的文件里**：一个能改自己钩子配置的
agent 没有边界，只有习惯。

不装任何东西也能试一下：

```sh
echo '{"session_id":"demo","tool_name":"Bash","tool_input":{"command":"rm -rf build"}}' \
  | python3 adapters/claude_code_hook.py; echo "exit $?"      # 先上报 irreversible，再问电路
```

工具怎么归类、哪些算不可逆，可以在接入页改，也可以写文件：`~/.c3s-circuit-agent/tool-classes.txt`
和 `irreversible-tools.txt`，适配器每次调用都会重读，不用重启。

### 任何 MCP 客户端

```sh
python3 adapters/mcp_proxy.py --agent my-agent --gate 'send_*' --gate 'trash_*' -- python3 your_server.py
```

`--gate` 点到名的调用会被拦，其余原样放过；**不给 `--gate` 就是全部都管**。被拒绝的调用以 MCP 的
`isError` 结果返回，模型看得懂。代理只看得见经过它的那一个服务器：客户端自带的工具（Claude Code
的 Bash、桌面应用自己的文件访问）和直接挂上的别的服务器都不经过它。

### BNB Agent SDK

```python
from adapters.bnbagent_boundary import BoundaryWalletProvider
wallet = BoundaryWalletProvider(inner_wallet, agent="my-agent")
```

每次 `sign_transaction` / `sign_typed_data` / `sign_message` 都是 `spend` 电路的一拍，电路不放行就签不
出任何东西。转账、approve、permit 一类的调用由包装层判成不可逆。**只把包装后的对象交给 agent 的
工具**，把内层钱包留在它拿不到的地方——同进程的代码总能绕过包装，这是这一层的实话。

### 接好之后自测一次

让它去做一件**你明确不允许**的事。它应该被拒绝，页面的流水（Activity）里应该出现一条带电路原话的
记录。如果它成功了，说明那条路没经过适配器——那就是你真正要堵的地方。

## 6. 手机和桌上那台设备

**先说清楚一件事。** 后台默认只绑 `127.0.0.1`，只有本机能连；`c3s up --lan` 是你自己选的。手机上
的页面拿的是一个**单独的 viewer 令牌**，不是操作者令牌：

* `c3s pair` 现场铸一个 viewer 令牌放进二维码（在 fragment 里，不发给服务器，页面读到就从地址栏
  清掉）。**操作者令牌不离开这台机器。**
* viewer 令牌的权力和一台配对过的 Cardputer 一样大，外加读 `/api/state`、`/api/manifest`、
  `/api/tasks`：能批准一个正在等、数字对得上的调用，能 block；**不能** unblock、写心跳、装规则、
  全停、恢复。页面会把手机用不了的控件禁掉并说明原因，并给一个"我有操作者令牌"的入口。手机的
  轮询**不算心跳**——只有设备自己取帧那条路算。
* `c3s pair --forget <设备 id>` 吊销一台手机。**今晚之前配过的手机会收到 403**，重新 `c3s pair`。
* 仍然没有 TLS。viewer 令牌在那个网络上是可读的，所以它被刻意做成读多写少的那一把；咖啡馆里
  不要开 `--lan`。
* 这一段是今晚改的（`docs/REDTEAM-2026-09-17.md` 的 F1）：页面原来每两秒轮询都把操作者令牌明文
  发出去，只要页面开着就一直发。

**手机**：`c3s up --lan` 之后 `c3s pair` 出二维码，扫了就到待确认页。

**Cardputer**（M5Stack 的掌上设备）：待确认清单显示在实体屏幕上，按实体回车键才写下批准。两种接法：

* USB：`c3s up --cardputer 1`（或环境变量 `REFLEX_CARDPUTER=1`）。中继跑在后台进程里，设备在位就是
  心跳，拔掉线、装了心跳规则的 agent 就全停。
* Wi-Fi：设备上进 Network 页（主菜单 8）选 pair by code，它显示四位数字；在后台这台机器上，用后台
  的 Python 跑 `python -m cardputer_relay open`，再 `python -m cardputer_relay confirm <那四位>`。数字
  错一次配对就取消，不会让你再猜。配好之后设备**只向外轮询、只上报一次按键，从不监听**，它的令牌
  严格比那根线弱：能批准一个正在等、数字对得上的调用，能 block，**不能 unblock、不能伪造心跳、不能
  全停或全部恢复**。设备的 flash 里有 Wi-Fi 凭据和设备令牌，转手前在设备上 Unpair 并忘掉网络。

**Telegram**：

```sh
TELEGRAM_TOKEN=... CONSOLE_URL=http://127.0.0.1:8765 TOOL_LAYER_CHATS=<你的 chat id> python3 bot_telegram.py
```

任何聊天都能 `/ask`、`/rules`、`/state`；只有 `TOOL_LAYER_CHATS` 里的聊天能 `/pending`、`/confirm`、
`/block`、`/stop`、`/resume`，也只有它们会在有东西开始等人时收到带按钮的提醒。按一下带的就是那一次
调用和它的两位数，调用变了、被别人批了、码重发了，按下去就明说并且什么都不写。**你的 agent 能打字
进去的聊天绝不能写进 `TOOL_LAYER_CHATS`**——一个"正在打字的人随手就能给"的 confirm，不是边界。

微信有一个中继（`adapters/wechat_relay.py`），目前只有模拟传输。三条通道各自证明了什么、证明不了
什么，在 `docs/CHANNELS.md`。

## 7. 出事了

页面右上角那个红键，以及 `c3s stop-all`：给后台当前认识的**每一个** agent 写下 `blocked`。

它是**闩锁**，不会自己松开。恢复是第二个刻意的动作：页面要你先点"解除全部封锁"，再把那个词打
出来（中文界面是"解除"，英文是 RESUME），再确认。故意麻烦。`c3s stop-all --resume` 是命令行的同一
个动作。恢复也会同时复位共享停机的闩锁——否则装了粘滞停机之后"恢复"是假的。

两件你应该知道的事：

* **它连没见过的也一起停。** 红键是一个闩锁，后台在问任何电路之前先看它。所以停下期间，一个
  **全新的 agent 名字**会被拒（`stopped: a person pressed stop-all…`），一个**没装电路的类**也会被
  拒（记录里仍然写着 `class_installed: false`，但不放行）。它曾经只是一份"当时认识的名字"清单，
  红队实测一个换名字的 agent 在第 4 拍拿到了放行；现在不会了。
* **代价说清楚**：停下期间新起的会话，到有人恢复之前是死的。这对一个急停键是对的取舍，但会让某个
  人意外一次。
* 想把名字本身钉死（这样"谁是同一个 agent"不只靠自觉），开 `REFLEX_REQUIRE_AGENT_TOKEN=1`，
  后台只答应人绑过的名字。绑法：`c3s token bind --agent <名字>`，令牌从 shell 的
  `REFLEX_AGENT_TOKEN` 读、或 `--stdin` 喂、或让它生成并只显示一次——**永远不作为命令行参数**，
  所以不会落进 shell 历史。`c3s token list` 看哪些名字绑了（只显示名字和时间，不显示哈希）。
  默认模式下的绑定是先到先得：本机上某个名字第一次带着 `REFLEX_AGENT_TOKEN` 来请求就绑上了，
  之后这个名字必须一直带同一个令牌，带错的记成 `spoof`。

## 8. 常见问题

**它拒错了怎么办？** 拒绝理由是电路的原话，照着读就知道缺什么：`irreversible, and no unspent
confirm` 是要人点头；`blocked is high` 是有东西把它拦了（红键？）；`halted until a confirm lifts it`
是停机闩锁还在；`cooldown: 7 ticks left of 8` 或 `commitment: 2 of 4` 是 `exec` 还挂着默认的果蝇
规则（§3）。

**为什么装了规则还是放行？** 看那条记录里有没有 `class_installed: false`——没装电路的类是不设防的
（共享停机仍然管它）。`exec` 永远有电路，其它类要你自己装。

**冷却八拍为什么没过八秒？** 拍不是时间，是调用次数。这里没有规则绑墙上的时钟。

**后台重启之后还算不算？** 规则算，agent 的状态算：花掉的额度还是花掉的、停机的还是停机的、被拦的
还是被拦的（实测：拦下、重启、再请求，仍是 `blocked is high`）。但有三样**不保存**：流水（Activity）
清空；待确认清单是从流水算出来的，所以也清空——正在等批准的调用要 agent 再问一次；两位数字随之重发。
一个类的状态只会恢复到**它被保存时那一颗电路**上，换了电路那个类从复位开始。状态文件损坏时后台
**拒绝启动**，而不是带着一个空边界起来。

**页面叫我输令牌，输哪个？** 首次启动打印的那一串，或 `c3s token`。只输在你自己的浏览器里；永远不要
把它给任何 agent 侧的东西。

---

# 第二部分 · 给开发的人

## 9. 一页架构

```
你的模型 ──(适配器)──► POST /api/request ──┐
                       只带 agent 自己的位 │
                                          ├──► 共享停机电路 ──► 这一类的电路 ──► 放行 / 拒绝
人 / 设备 / 工具层 ──► POST /api/tool ─────┘                                      │
                       带 agent 不该有的位                            链上只读复算（可选）
                       人的那几位要操作者令牌
```

* 后台是**一个进程、一个端口**，默认只绑 127.0.0.1（`--lan` 才绑 0.0.0.0）。标准库的 HTTP 服务，
  唯一的第三方依赖是电路仓要的 numpy。
* 每一类工具一颗电路，外加一颗所有类共享的 `halt`。`exec` 永远有电路（默认是 `FLY_DEFAULT`：
  `min_gap_ticks=8, commit_ticks=4, forbid_when_blocked`）。
* 规则不是解释执行的：它们**编译成 netlist**，逐行核对，在可达状态上证明，然后才被安装。
  磁盘上保存的是规则的设置，不是电路；启动时**重新编译、重新证明**，不信任盘上的东西。
* 电路仓（`C3S_REPO`，默认 `~/work/c3s-reflex`）提供 `c3s.policy`、`c3s.exhaust`、`c3s.netlist`。
  后台自己不实现逻辑语义。

## 10. 两条通道（整个项目的支点）

| 位 | 谁写 | 靠它的规则是 |
| --- | --- | --- |
| `request`、`intent` | **agent 自己** | 成本——它永远可以选择付 |
| `blocked`、`confirm`、`confirm_b`、`heartbeat` | **人**（要操作者令牌；`heartbeat` 也可以是设备在位） | 边界 |
| `irreversible`、`failed` | **工具层**（不要令牌，因为只会更严） | 边界 |

软件没办法证明发请求的到底是谁。所以这条分界被画在**接口形状**上，而不是藏在文档里：两个端点、
两套位、其中一套要令牌。`docs/API.md` 的表格是权威。

`irreversible` 和 `failed` 不要令牌，是刻意的：它们只可能让判决**更严**，让适配器无成本地上报是
对的。反过来，任何能让判决更松的位——`confirm`、`blocked: 0`——都要令牌。

## 11. 十一条规则，以及后台装了几条

语义的权威在电路仓 `c3s/policy.py` 的 `Policy` docstring，README 的表格是它的摘要。要点：

* 所有计数上限 255；整个定义域（输入 + 状态位）超过 26 位就拒绝编译（`exhaust.MAX_EXHAUSTIVE_BITS`），
  不会悄悄降级成抽样。
* `max_grants=0` 是**不限次**。"一次都不行"要用**整类拒绝**（`deny_all`），那是没有电路的一种情形。
* `min_gap_ticks` 是倒数，不是已过拍数——所以第一次放行不被挡，之后严格隔 n 拍。（数已过拍数会挡掉
  第一次并多隔一拍，那是 P1 监控器当年的差一错误。）
* `confirm_per_irreversible`：不可逆的一拍要一个"上次不可逆放行之后到达的"confirm，**并且这次放行会
  花掉它**；可逆的放行既不需要也不消耗。
* `sticky_block` / `heartbeat_ticks` 停机之后，**那一拍带 confirm 的也不放行**——confirm 用来解闩，
  不用来放行。
* `two_key`：两把钥匙都要在上次放行之后到过，放行花掉两把。后台只有一个操作者令牌，所以它证明的是
  "两次写入"，不是"两个人"。
* `trip_after_refusals` 是唯一**读电路自己判决**的规则，也是这份文档里唯一一条**后台刻意不提供**的
  规则。原因值得完整写下来，因为它正是这个项目想避免的那类事：它编译得出来，**安全那一半也证明过**
  ——跳闸期间不放行——但**它的复位是坏的**。跳闸期间每一拍都必然是拒绝，于是带着人那次 confirm 的
  那一拍又把跳闸重新触发了一次，`and not confirm` 那条分支永远轮不到。对着库自己的参考实现实测：
  第 4、6、9 拍各带一次 confirm，`refused_tripped` 从头到尾是 1，连"这一拍没有请求"也解不开。
  库里的证明没抓到，是因为监控只断言了安全那一半，从没断言活性那一半（"一次 confirm 能解开它"）。
  一条拒绝理由写着"一次 confirm 复位"、实际意思是"永远不会"的规则，比没有这条规则更糟，所以在电路
  修好、并且有活性监控守着之前，后台不提供它（2026-09-17 查明）。

**加一条规则要动的地方**，正好是上面这个缺口教的：电路仓里是 `Policy` 的字段与 docstring、位宽
属性、`layout()`、参考实现、`reasons()`、`properties()` 里的那一项；后台里是 `_settings`（否则不持久）、
`policy_from`（否则装不上）、`console.py` 开头把拒绝原话映射到位的正则（否则待确认页不知道等哪一位）；
页面里是编辑器的输入框和 `domainOf()`。少一处，就是 `trip_after_refusals` 今天的样子。

**反向对照不能是空的**——如果你的负例在任何配置下都没机会被放行，那它什么都没证明。
`trip_after_refusals` 第一版就踩了这个坑：负例配的是花额度的预算规则，于是"blocked 掉下来之后的那
一拍"根本不可能放行，48 个违例是把规则改成 `forbid_when_blocked=True` 之后才抓到的。

## 12. 一次决策的生命周期

1. 适配器 `POST /api/request`，带 `agent`、`class`、`intent`、`reason`，可选 `effect`（结构化的"这次
   会改什么"，只用于显示，永远不影响判决）和 `task`（一个人之前在任务页登记的 id）。
2. 名字与令牌：走局域网的请求名字必须已绑定并带自己的令牌；本机上没绑过的名字照常工作，页面标
   "unbound"。
3. 任务只做减法：id 不存在或已关闭的任务，请求直接被拒，不进电路；`max_grants` 到了任务自己关闭。
   任务 id 是写在流水上的一个标签，**不能给任何位赋值**，有测试断言"带不带任务 id，判决、理由、输入、
   电路状态全都一样"。
4. 先跑**共享停机电路**；停机了就不用问类电路，理由里带 `halted (shared halt circuit): …`。
5. 再跑这一类的电路。类没装电路 = 不设防（记录里 `class_installed: false`），停机仍然管它。
6. 事件位（`confirm`、`confirm_b`、`irreversible`、`heartbeat`、`failed`）**被这一拍消耗**；`blocked`
   是电平，留着。
7. 判决落进流水；`pending[]` 由后台算出来（带两位数字、绑定的调用、等的是哪一位）。拒绝回复里如果
   有一个 confirm 绑在别的调用上，`confirm_waiting_for` 会点名它。
8. 可选的**链上只读复算**在后台线程里跑（`CHAIN_WAIT_S`，默认 1.5 秒），同一份 netlist 交给公共 BSC
   节点 `eth_call` + 状态覆盖，结果写进回复的 `chain` 字段。**什么都不部署。**

早期有个 bug 值得记住：链上复核在锁外读 netlist，撕裂读会显示 4/40 不一致。修法是把 netlist、状态
位宽和输入数一起带进判决结果里，而不是回头再读。

## 13. 三个令牌，不能互相代替

| 令牌 | 说什么 | 存在哪 |
| --- | --- | --- |
| **操作者令牌** | "我是那个人" | `~/.c3s-circuit-agent/operator-token`（600），或环境变量 `REFLEX_OPERATOR_TOKEN` |
| **agent 令牌** | "我还是上次那个程序" | `agents.json`（只存 sha256），适配器用 `REFLEX_AGENT_TOKEN` 发，头是 `X-Reflex-Agent-Token` |
| **设备令牌** | "我是那台配对过的 Cardputer" | `devices.json`（只存 sha256），头是 `X-Reflex-Device-Token` |

比较一律用 `hmac.compare_digest`。**拿着操作者令牌不等于就是某个 agent**：一个已绑定的名字带错令牌
是 403，记成 `spoof`。想把名字收回来是 `c3s token rotate --agent <名字>`——删掉绑定，下一次带令牌的请求
重新绑——不是借用。

绑定是"先到先得"（trust on first use），只在本机上允许，走局域网的第一次请求不会替它建绑定。
`REFLEX_REQUIRE_AGENT_TOKEN=1` 关掉 HTTP 建绑定这条路，只答应人绑过的名字；这个模式下绑名字只能
在本机用 `c3s token bind --agent <名字>`（令牌走环境变量、`--stdin` 或现场生成，不进 shell 历史），
`c3s token list` 列出已绑的名字。

红队还确认了一条（F2）：一个已绑定的 agent 令牌在局域网上能读整份 `/api/state`——别的 agent 的待确认
内容和流水都在里面。它本来只该用来认自己的请求。

## 14. 写一个适配器

最小实现就是一次 HTTP：

```python
import json, urllib.request
def may_i(agent, cls, reason, irreversible=False):
    if irreversible:   # 工具层的位，不要令牌，因为只会更严
        post("/api/tool", {"agent": agent, "irreversible": 1})
    v = post("/api/request", {"agent": agent, "class": cls, "intent": 1, "reason": reason})
    return v["granted"], v.get("why", []), v.get("confirm_waiting_for", [])
```

要做对的四件事：

1. **连不上就拒绝**（fail closed）。`REFLEX_FAIL_OPEN=1` 才放过——那是"我选择没有边界"，适配器不会
   替你选。
2. **`reason` 要写调用的真实内容**，不是人话描述。Bash 就写命令本身——批准要绑的是这个。（钩子早期
   按 `description` 写，于是人批准的和实际跑的不是一回事。）
3. **自保护**：拒绝任何指向后台 host:port 的网络工具调用。这是**纵深防御**，真正的防线是操作者令牌
   ——字面量匹配用引号拆开就能绕过，红队也确认它只认默认端口和几种写法。
4. **别把操作者令牌交给 agent 侧的任何东西。**

`adapters/README.md` 有全部环境变量；`.env.example` 有一份带注释的清单。

## 15. 接口速查

权威是 `docs/API.md`（I-1…I-5 是**冻结接口**，改动要在那里登记）。

| 端点 | 谁调 | 要令牌 |
| --- | --- | --- |
| `POST /api/request` | agent 的适配器 | 不要；名字绑过就要带 agent 令牌，走局域网必须带 |
| `POST /api/tool` — `irreversible`、`failed` | 适配器（工具层） | 不要 |
| `POST /api/tool` — `confirm`、`confirm_b`、`blocked`、`heartbeat` | 人（页面、聊天） | 操作者令牌；聊天还要带两位数 `code` |
| `POST /api/tool` 带 `X-Reflex-Device-Token` | 配对过的 Cardputer | 设备令牌；只能 confirm 正在等的调用（带 code）和 `blocked: 1` |
| `POST /api/policy` | 人 | 要；`deny_all: true` 整类拒绝，`remove: true` 拆掉电路（`exec` 拆不掉） |
| `POST /api/stop-all` · `/api/resume-all` | 人 | 要 |
| `POST /api/task` · `POST /api/task/<id>/close` | 人 | 要。登记一件差事（文本、允许的类、最多几次放行）、关掉它。**不是** `docs/API.md` I-5 描述的那个跑 `claude -p` 的入口——`runner` 等字段只是原样存着，后台不启动任何东西 |
| `GET /api/tasks` · `GET /api/task/<id>` | 本机 | 走局域网要 |
| `GET /api/state` · `GET /api/manifest` | 本机 | 走局域网要操作者令牌或已绑定的 agent 令牌 |
| `GET /api/classes` · `POST /api/classes` | 人 | GET 本机免、局域网要；POST 要。写的是 `tool-classes.txt` / `irreversible-tools.txt` |
| `POST /api/hooks/claude-code` | 人，只限本机 | 要。预览并写入 Claude Code 的两段钩子配置，先备份，解析不了就拒绝 |
| `GET /api/device/frame` | 配对过的设备 | 设备令牌；这次轮询本身就是心跳 |
| `POST /api/device/pair/begin` · `/confirm` · `/forget` | 人 | 要 |
| `POST /api/device/pair` | 正在认领配对窗口的设备 | **故意不要**：认领之后它必须在屏幕上显示四位数，人来核对 |

跨站防护：检查 `Host`（含本机各个局域网地址的白名单）和 `Origin`，只收 `application/json`，拒绝被
重绑定的 Host 名——都有测试。所有错误体都是 `{"error": "<一句人能照着做的话>"}`。

## 16. 跑测试

```sh
PY=~/work/c3s-cache/.venv/bin/python     # 本机 python 是 Python 2
# 电路仓：纯离线
cd ~/work/c3s-reflex && $PY -m pytest -q
# 后台：要一个活着的后台，端口可换
cd ~/work/reflex-console
CONSOLE_PORT=8777 REFLEX_CONFIG_DIR=/tmp/t REFLEX_OPERATOR_TOKEN=t nohup $PY -u console.py > /tmp/c.log 2>&1 &
REFLEX_CONSOLE=http://127.0.0.1:8777 REFLEX_OPERATOR_TOKEN=t $PY -m pytest -q tests
```

测试读 `REFLEX_CONSOLE` / `CONSOLE_PORT`，所以两个 checkout 可以各跑一套、互不干扰；`REFLEX_CONFIG_DIR`
指到临时目录，别让测试碰你真实的 `~/.c3s-circuit-agent`。起服务一律**写日志文件**，不要把服务管道
接给 `tail`。BNB SDK 那组测试要 SDK 自己的 venv（`adapters/README.md` 末尾）。要刷固件先把占着串口的
中继停掉。

## 17. 链上那一半

* **只读复算**：同一份 netlist 交给公共 BSC 节点，用 `eth_call` + 状态覆盖，**永不部署**。这部分只
  依赖电路仓已发布的 `main`。
* **ERC-8004 边界清单**：`c3s/erc8004.py`（keccak 走 Foundry 的 `cast keccak`，没有 `cast` 时后台照跑，
  哈希那一栏说算不出来）、`scripts/boundary_manifest.py`、`scripts/validate_boundary.py`（分数只有 100
  或 0），`lineage.parent` 指向果蝇逃逸核心的摘要。
* **`ReflexModule`**：一个 Safe module，`docs/ONCHAIN-SELF-DEPLOY.md`。边界没放行的交易执行不了。
  **读者自己部署，我们不持钥匙。**

后两项都在电路仓的 **`boundary` 分支**上，**那个分支还没有推到 GitHub**。对着已发布的 `main` 跑的
后台，`GET /api/manifest` 会因为找不到 `c3s.erc8004` 而报错，页面的清单面板也一样。

规矩：**这个项目里没有钱包、没有私钥、没有助记词**，任何链上工作只在**本地分叉**上做，文档必须写明
"本地分叉、未广播"。

## 18. 改这个项目的时候

* 提交信息用英文、手写、说清**为什么**。发布前跑 `pytest` 和 `~/work/c3s-cache/guard/scan.sh . --history`。
* 发二进制之前用 `strings` 扫一遍本机绝对路径。
* **一台配对过的 Cardputer 的 flash 是秘密**（NVS 里有 Wi-Fi 凭据和设备令牌）——永远不要发布 flash
  dump，发布镜像只能从未配对的树里构建。
* 所有面向用户的字串说"反射弧 / reflex arc"，不说"大脑 / brain"（免责声明和引用除外）。
* 数据只用 MaleCNS（CC-BY）。**不用任何 FlyWire 派生物**（CC BY-NC）。
* 改了 `docs/API.md` 里冻结的接口，先告诉协调者：五条工作流都读它。

---

## 还差什么

诚实的那一栏在 `docs/REVIEW-2026-09-16-gap-to-v1.md`（离 v1.0 差什么）和 `docs/REDTEAM-2026-09-17.md`
（红队打了什么、什么破了、什么没破）。这份指南描述的是**现在真的能跑的东西**；还没落地的，上面写的
是"还没有"，不是"即将"。
