# C3S Circuit Agent 指南

这一份同时写给两种人。**第一部分**给要用它的人：装上、写下边界、第一次批准、日常怎么过。
**第二部分**给要改它、接它、审它的人：架构、语义、接口、怎么加一条规则。

中间那条横线是分界。只想用的人可以在那里停下。

---

# 第一部分 · 给用的人

## 1. 五分钟装好

需要：macOS 或 Linux、Python 3.11 以上、一个终端。不需要钱包、不需要账号、不需要网。

```sh
git clone <本仓库> && cd reflex-console
sh install.sh
```

它把 `c3s` 命令装进一个隔离的工具环境（优先 uv，其次 pipx，最后 `~/.c3s-circuit-agent/venv`
里的一个普通 venv），然后启动后台并打开页面。**不碰系统 Python，不用 sudo。**

装完你会看到一串**操作者令牌**。它就是"人"这个角色的钥匙 —— 后面所有需要人点头的动作都要它。
它同时存在 `~/.c3s-circuit-agent/operator-token`，权限 600。

```sh
c3s status        # 在不在跑、有什么在等人、日志在哪
c3s token         # 再看一次令牌；c3s token rotate 换一个
c3s down          # 停掉
```

想让它开机自启：`c3s up --service`（装一个 launchd agent，被杀了也会拉回来）。

## 2. 你的第一条边界

打开页面的**边界（Boundaries）**。工具被分成四类，加一个所有类共享的停机电路：

| 类 | 装什么 | 例子 |
| --- | --- | --- |
| `spend` | 花钱、签名、转账 | 建议：**整类拒绝** |
| `message` | 发出去的消息 | 要人点头 |
| `files` | 写、删、覆盖 | 要人点头 |
| `exec` | 跑命令 | 连续失败就跳闸 |
| `halt` | 共享停机 | 一拍 blocked 就全停，直到人解除 |

挑一个模板，点编译。**先看证据面板再点安装。** 它会告诉你：

* **多少个 NAND 门、几个锁存器** —— 这颗电路有多大。
* **核对了多少行** —— 这是它输入定义域的全部行数。这不是抽样。
* **多少个可达状态、证明了多少行** —— 每条规则在每个到得了的状态里都验过。
* **每条规则是否都成立** —— 这一行是 `true` 才安装。

如果某件事你的答案是"绝对不行"，不要去写一个很大的数字（`max_grants=0` 是**不限次**，
不是零次）。选**整类拒绝**：编译出来的电路里没有任何通往放行的路径。

## 3. 第一次批准，以及那两位数字

接上模型之后（下一节），它迟早会撞到边界。**待确认（Approvals）** 页会出现一条：

```
files · 第 1 拍                                        代码 47
rm -rf ./build
拒绝原因（电路原话）：irreversible, and no unspent confirm
[ 批准这一次 ]  [ 拦下这个 agent ]
```

旁边那个 **47** 是这一次调用的两位数字。你批准的时候要把它输进去。这不是防手滑的摆设，
它是**把你这次批准绑死在这一次调用上**的机制：

* 一次批准只对**这一次调用**有效（按调用内容绑定）。
* 用掉就没了。它不会变成长期通行证。
* 它挪不到别的调用上 —— 哪怕那个 agent 紧接着换了个更危险的请求。

早期版本没有这条，代价是真实的：一次给"回邮件"的批准，被下一个"删邮件"的调用花掉了。
现在不会了，而且有测试锁着。

**模型改了措辞怎么办？** 模型重试时经常换一次说法，于是那次批准对不上它了。拒绝理由里会
写明"正在等的是哪一次调用"，让模型把原话重发一遍即可。

## 4. 日常：把你的模型接进来

页面的**接入（Connect）**有三条路，选你在用的那条。

### Claude Code

把 `adapters/claude_code_hook.py` 配成 PreToolUse 钩子，`claude_code_post_hook.py` 配成
PostToolUse（后者让"失败"这件事能进电路，跳闸规则才有意义）。Bash、Write、Edit 和所有 MCP
工具都会经过它。

工具怎么归类，可以在页面上改，也可以写文件：`~/.c3s-circuit-agent/tool-classes.txt` 和
`irreversible-tools.txt`，适配器每次调用都会重读，不用重启。

### 任何 MCP 客户端

```sh
adapters/mcp_proxy.py --agent my-agent --gate 'send_*' --gate 'trash_*' -- python3 your_server.py
```

只有 `--gate` 点到名的调用会被拦，读操作原样放过。被拒绝的调用以 MCP 的 `isError` 返回，
模型看得懂。

### BNB Agent SDK

```python
from adapters.bnbagent_boundary import BoundaryWalletProvider
wallet = BoundaryWalletProvider(inner_wallet, agent="my-agent")
```

spend 电路不放行，就签不出任何东西。**只把包装后的对象交给 agent 的工具**，把内层钱包留在
它拿不到的地方 —— 同进程的代码总能绕过包装，这是这一层的实话。

### 接好之后自测一次

让它去做一件**你明确不允许**的事。它应该被拒绝，页面上应该出现一条带电路原话的记录。
如果它成功了，说明那条路没经过适配器 —— 那就是你真正要堵的地方。

## 5. 手机和桌上那台设备

**手机**：`c3s up --lan` 之后 `c3s pair` 出二维码。扫了就到待确认页。令牌放在 URL 的
fragment 里（fragment 不会发给服务器），页面拿到后立刻从地址栏清掉。它毕竟在你的 Wi-Fi
上走过一次 —— 介意就用 `c3s pair --no-token`，令牌完全不上网络，到页面后手动粘一次。

**Cardputer**（M5Stack 掌上设备）：待确认清单显示在实体屏幕上，按实体回车键才写下批准。
走 USB 线（`REFLEX_CARDPUTER=1`），或者走 Wi-Fi：设备菜单 8 是 Network，配对时设备显示四位
数字，你在页面上把它敲进去。设备**只向外轮询、只上报一次按键，从不监听**。它的令牌和那根线
一样大：能批准一个正在等、数字对得上的调用，能 block，**永远不能 unblock**。

**Telegram**：把你自己的 chat id 写进 `TOOL_LAYER_CHATS`，批准、拦下、全停只在那里有效。
**你的 agent 能打字进去的聊天绝不能写进来** —— 一个"正在打字的人随手就能给"的 confirm，
不是边界。

## 6. 出事了

页面右上角那个红键，以及 `c3s stop-all`：给后台当前认识的**每一个** agent 写下 `blocked`。

它是**闩锁**。它不会自己松开。恢复是第二个刻意的动作：页面要你把那个词打出来。

两件你应该知道的事：

* **恢复会同时解除共享停机的闩锁。** 否则装了粘滞停机之后，"恢复"这个动作是假的 ——
  这正是 2026-09-16 收尾时发现并修掉的一个真 bug。
* **按下红键之后才第一次出现的 agent，不在拦下的名单里**（页面会写"m 个里拦下了 n 个"）。
  这意味着**一个换名字的 agent 能从红键底下走出去**，除非你开严格令牌模式：

  ```sh
  REFLEX_REQUIRE_AGENT_TOKEN=1        # 只答应人绑过的名字
  c3s token bind --agent my-agent     # 绑一个
  ```

  想要真正的"拦下一切"，就开它。

## 7. 常见问题

**它拒错了怎么办？** 拒绝理由是电路的原话，照着读就知道缺什么：缺 confirm，就是要人点头；
`blocked is high`，就是有东西把它拦了（红键？粘滞停机？）；`halted until a confirm lifts
it`，就是停机闩锁还在。

**为什么装了规则还是放行？** 看那条记录里有没有 `class_installed: false` —— 没装电路的类
是不设防的（共享停机仍然管它）。

**冷却八拍为什么没过八秒？** 拍不是时间，是调用次数。这里没有规则绑墙上的时钟。

**后台重启之后还算不算？** 算。花掉的额度还是花掉的、停机的还是停机的、被拦的还是被拦的 ——
但一个类的状态只会恢复到**它被保存时那一颗电路**上；换了电路，那个类从复位开始，就像刚装一样。
状态文件损坏时后台**拒绝启动**，而不是带着一个空边界起来。

---

# 第二部分 · 给开发的人

## 8. 一页架构

```
你的模型 ──(适配器)──► POST /api/request ──┐
                       只带 agent 自己的位 │
                                          ├──► 每类一颗编译好的电路 ──► 放行 / 拒绝
人 / 设备 / 工具层 ──► POST /api/tool ─────┘        + 共享停机电路
                       带 agent 不该有的位
                       （要操作者令牌）
```

* 后台是**一个进程、一个端口**，默认只绑 127.0.0.1（`--lan` 才绑 0.0.0.0）。
* 每一类工具一颗电路，外加一颗所有类共享的 `halt`。
* 规则不是解释执行的：它们**编译成 netlist**，逐行核对，在可达状态上证明，然后才被安装。
* 电路仓库（`C3S_REPO`，默认 `~/work/c3s-reflex`）提供 `c3s.policy` 和求值器。后台自己
  不实现逻辑语义。

## 9. 两条通道（整个项目的支点）

| | 谁写 | 靠它的规则是 |
| --- | --- | --- |
| `request`、`intent` | **agent 自己** | 成本 —— 它永远可以选择付 |
| `blocked`、`confirm`、`confirm_b`、`heartbeat` | **人**（要操作者令牌） | 边界 |
| `irreversible`、`failed` | **工具层**（不要令牌，因为只会更严） | 边界 |

软件没办法证明发请求的到底是谁。所以这条分界被画在**接口形状**上，而不是藏在文档里：两个
端点、两套位、其中一套要令牌。`docs/API.md` 的表格是权威。

`irreversible` 和 `failed` 不要令牌，是刻意的：它们只可能让判决**更严**，让适配器无成本地
上报是对的。

## 10. 十一条规则各自证明什么

语义的权威在电路仓库 `c3s/policy.py` 的 `Policy` docstring。要点：

* `max_grants=0` 是**不限次**。"一次都不行"要用**整类拒绝**（`deny_all`）。
* `min_gap_ticks` 是倒数，不是已过拍数 —— 所以第一次放行不被挡，之后严格隔 n 拍。
  （数已过拍数会挡掉第一次并多隔一拍，那是 P1 监控器当年的差一错误。）
* `confirm_per_irreversible`：不可逆的一拍要一个"上次不可逆放行之后到达的"confirm，
  **并且这次放行会花掉它**；可逆的放行既不需要也不消耗。
* `sticky_block` / `heartbeat_ticks` 停机之后，**那一拍带 confirm 的也不放行** ——
  confirm 用来解闩，不用来放行。
* `trip_after_refusals` 是唯一**读电路自己判决**的规则：连续被拒 n 次就停机，而不是让
  agent 一直撞边界。没有 request 的一拍不算被拒。
* 域宽有上限（≤24 位）：规则组合太大会被拒绝编译，而不是悄悄降级。

加一条规则要动四个地方：`Policy` 的字段与 docstring、位宽属性、参考实现（reference）、
监控器（violations 里那一项）。**反向对照不能是空的** —— 如果你的负例在任何配置下都没机会
被放行，那它什么都没证明。`trip_after_refusals` 第一版就踩了这个坑：负例配的是花额度的
预算规则，于是"blocked 掉下来之后的那一拍"根本不可能放行，48 个违例是把规则改成
`forbid_when_blocked=True` 之后才抓到的。

## 11. 一次决策的生命周期

1. 适配器 `POST /api/request`，带 `agent`、`class`、`intent`、`reason`，可选 `effect`
   （结构化的"这次会改什么"）和任务 id。
2. 后台先跑**共享停机电路**；停机了就不用问类电路，理由里会带 `halted (shared halt
   circuit): …`。
3. 再跑这一类的电路。类没装电路 = 不设防（记录里 `class_installed: false`），但停机仍然管它。
4. 事件位（`confirm`、`confirm_b`、`irreversible`、`heartbeat`、`failed`）**被这一拍消耗**；
   `blocked` 是电平，留着。
5. 判决落进流水；`pending[]` 由后台算出来（带两位数字、绑定的调用、等的是哪一位）。
6. 可选的**链上只读复算**在后台线程里跑（`CHAIN_WAIT_S`，默认 1.5 秒），同一份 netlist
   交给公共节点。**什么都不部署。**

早期有个 bug 值得记住：链上复核在锁外读 netlist，撕裂读会显示 4/40 不一致。修法是把
netlist、状态位宽和输入数一起带进判决结果里，而不是回头再读。

## 12. 三个令牌，不能互相代替

| 令牌 | 说什么 | 存在哪 |
| --- | --- | --- |
| **操作者令牌** | "我是那个人" | `~/.c3s-circuit-agent/operator-token`（600），或 `REFLEX_OPERATOR_TOKEN` |
| **agent 令牌** | "我还是上次那个程序" | `agents.json`（存哈希），适配器用 `REFLEX_AGENT_TOKEN` 发 |
| **设备令牌** | "我是那台配对过的 Cardputer" | `devices.json` |

比较一律用 `hmac.compare_digest`。**拿着操作者令牌不等于就是某个 agent**，反过来也一样 ——
两种混用都会被 403，错误信息会说清是哪一种。想把名字收回来是**轮换绑定**，不是借用。

`REFLEX_REQUIRE_AGENT_TOKEN=1` 是严格模式：只答应人绑过的名字。这个模式下 HTTP 绑定这条路
关掉，只能用 `c3s token bind`（本机）。**走局域网的请求，名字永远必须已绑定并带自己的令牌**
—— "没绑过的名字照样能用"是给本机程序的兼容承诺，不是给 Wi-Fi 的邀请。

## 13. 写一个适配器

最小实现就是一次 HTTP：

```python
import json, urllib.request
def may_i(agent, cls, reason, irreversible=False, token=None):
    if irreversible:   # 工具层的位，不要令牌，因为只会更严
        post("/api/tool", {"agent": agent, "irreversible": 1})
    v = post("/api/request", {"agent": agent, "class": cls, "intent": 1, "reason": reason})
    return v["granted"], v.get("why", [])
```

要做对的四件事：

1. **连不上就拒绝**（fail closed）。`REFLEX_FAIL_OPEN=1` 才放过 —— 那是"我选择没有边界"。
2. **`reason` 要写调用的真实内容**，不是人话描述。Bash 就写命令本身 —— 批准要绑的是这个。
   （钩子早期按 `description` 写，于是人批准的和实际跑的不是一回事。）
3. **自保护**：拒绝任何指向后台 host:port 的网络工具调用。这是**纵深防御**，真正的防线是
   操作者令牌 —— 曾经有人用 `/api/to''ol` 绕过了字面量匹配。
4. **别把操作者令牌交给 agent 侧的任何东西。**

`adapters/README.md` 有全部环境变量；`.env.example` 有一份带注释的清单。

## 14. 接口速查

权威是 `docs/API.md`（I-1…I-5 是**冻结接口**，改动要在那里登记）。

| 端点 | 谁调 | 要令牌 |
| --- | --- | --- |
| `POST /api/request` | agent 的适配器 | 不要（可带 agent 令牌） |
| `POST /api/tool` | 人 / 设备 / 工具层 | 人的那几位要操作者令牌 |
| `POST /api/policy` | 人 | 要 |
| `POST /api/stop-all` · `/api/resume-all` | 人 | 要 |
| `GET /api/state` · `/api/manifest` | 谁都行（本机） | 走局域网要 |
| `GET /api/classes` · `POST /api/classes` | 人 | GET 本机免、局域网要；POST 要 |
| `GET /api/device/frame` | 配对过的设备 | 设备令牌 |

跨站防护：检查 Host（含本机各个局域网地址的白名单）和 Origin，拒绝 `text/plain` 和表单体，
拒绝被重绑定的 Host 名 —— 都有测试。

## 15. 跑测试

```sh
PY=~/work/c3s-cache/.venv/bin/python     # 本机 python 是 Python 2
# 电路仓库：纯离线
cd ~/work/c3s-reflex && $PY -m pytest -q
# 后台：要一个活着的后台，端口可换
cd ~/work/reflex-console
CONSOLE_PORT=8765 REFLEX_OPERATOR_TOKEN=... nohup $PY -u console.py > /tmp/c.log 2>&1 &
$PY -m pytest -q tests
```

测试读 `REFLEX_CONSOLE` / `CONSOLE_PORT`，所以两个 checkout 可以各跑一套、互不干扰。
起服务一律**写日志文件**，不要把服务管道接给 `tail`。要刷固件先把占着串口的中继停掉。

## 16. 链上那一半

* **只读复算**：同一份 netlist 交给公共 BSC 节点，用 `eth_call` + state override，
  **永不部署**。
* **ERC-8004 边界清单**：`c3s/erc8004.py`（keccak 走 `cast keccak`）、
  `scripts/boundary_manifest.py`、`scripts/validate_boundary.py`（分数只有 100 或 0）。
  `lineage.parent` 指向核心摘要。
* **`ReflexModule`**：一个 Safe module，`docs/ONCHAIN-SELF-DEPLOY.md`。边界没放行的交易
  执行不了。**读者自己部署，我们不持钥匙。**

规矩：**这个项目里没有钱包、没有私钥、没有助记词**，任何链上工作只在**本地分叉**上做，
文档必须写明"本地分叉、未广播"。

## 17. 改这个项目的时候

* 提交信息用英文、手写、说清**为什么**。发布前跑 `pytest` 和 `~/work/c3s-cache/guard/scan.sh . --history`。
* 发二进制之前用 `strings` 扫一遍本机绝对路径。
* **一台配对过的 Cardputer 的 flash 是秘密**（NVS 里有 Wi-Fi 凭据和设备令牌）——
  永远不要发布 flash dump，发布镜像只能从未配对的树里构建。
* 所有面向用户的字串说"反射弧 / reflex arc"，不说"大脑 / brain"（免责声明和引用除外）。
* 数据只用 MaleCNS（CC-BY）。**不用任何 FlyWire 派生物**（CC BY-NC）。

---

## 还差什么

诚实的那一栏，写在 `docs/RELEASE-READINESS.md` 和 `docs/REVIEW-2026-09-16-gap-to-v1.md`
里。这份指南描述的是**现在真的能跑的东西**；还没落地的东西不会出现在上面。
