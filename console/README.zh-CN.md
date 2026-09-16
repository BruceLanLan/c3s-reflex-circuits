# C3S Circuit Agent

**把「我的 agent 绝对不能做什么」写下来。它会编译成一颗电路，电路被穷尽证明，
然后你的模型每一次工具调用，都要先过这颗电路。**

不是提示词，不是能被模型绕过去的 system message。是一颗 NAND/锁存器电路：在它输入定义域的
**每一行**上核对过，在它**每一个可达状态**里证明过每一条规则 —— 然后在你的模型每次动手之前
被问一次。

[English](README.md) · [使用指南](docs/GUIDE.md) · [接口](docs/API.md) · [安全](SECURITY.md)

---

## 它成立的时候是什么样

装一条规则 —— *不可逆的动作要人点头，一次点头只管一次动作* —— 后台回给你的是这个。不是"我保证
有用"，而是证据本身：

```json
{
  "rules": ["nothing is granted while blocked is high",
            "an irreversible grant needs its own confirm, and spends it"],
  "circuit": { "nand": 18, "latch": 1, "bytes": 130, "depth": 11 },
  "checked": { "rows": 64, "matches_reference": true,
               "reachable_states": 2, "states_possible": 2,
               "rows_proven": 256, "every_rule_holds": true }
}
```

64 行就是它输入定义域的全部：没有哪种输入是这颗电路没被喂过的。256 行被证明，是那条规则在
它能到达的每一个状态里都被验过一遍。`every_rule_holds: true` 不是"测试通过"，是**没有反例**。

然后你的 agent 要删东西，而钥匙在人手上：

```
第 1 拍   files   rm -rf ./build     拒绝 —— irreversible, and no unspent confirm
          ↑ 人在页面 / Cardputer / Telegram 上，批准了这一次调用
第 2 拍   files   rm -rf ./build     放行
第 3 拍   files   rm -rf ./build     拒绝 —— irreversible, and no unspent confirm
```

第三行才是重点。那次批准被人读过、批过的那一次调用**花掉了**。它没有变成一张长期通行证，
也没法被挪到别的调用上 —— 一次批准绑死在它被展示时的那一次调用上。

## 装上

```sh
git clone <本仓库> && cd reflex-console
sh install.sh            # 把 c3s 命令装进一个隔离环境，然后启动
```

页面在 http://127.0.0.1:8765。不往系统 Python 里装任何东西，全程不用 `sudo`。

```sh
c3s up --lan       # 同一个 Wi-Fi 下手机也能打开
c3s pair           # 给手机扫的二维码
c3s status         # 在不在跑、有什么在等人
c3s stop-all       # 立刻拦下所有 agent
c3s down
```

首次启动会打印一个**操作者令牌**，同时存在 `~/.c3s-circuit-agent/operator-token`（权限
600）。它是人的钥匙：装规则、写人的那几位（`confirm`、`confirm_b`、`blocked`、
`heartbeat`）都要它。页面只问一次，只存在那个浏览器里。**你模型的适配器永远拿不到它** ——
这是"能连上后台的 agent 也没法自己批准自己"唯一的依据。

## 三步

1. **边界（Boundaries）** —— 给每类工具挑一个模板。*绝不转钱*（spend 整类拒绝）、
   *删东西要人点头*（files）、*发消息要人点头*（message）、*连续失败就停*（exec）、
   *停机 + 心跳*（所有类共享）。装上之后，证据面板会显示门数、逐行核对了多少行、
   每条规则是否都成立。
2. **接入（Connect）** —— 把你本来就在用的模型接进来：
   * **Claude Code** —— `adapters/claude_code_hook.py` 作为 PreToolUse 钩子（再加一个
     PostToolUse 钩子，让失败也能进电路）。Bash、Write、Edit 和所有 MCP 工具都走它。
   * **任何 MCP 客户端** —— `adapters/mcp_proxy.py -- <你的 MCP 服务器>`。只管会改东西的
     调用，读操作原样放过。
   * **BNB Agent SDK** —— 把钱包包进
     `adapters/bnbagent_boundary.BoundaryWalletProvider`。spend 电路不放行，就签不出去。
3. **待确认（Approvals）** —— 什么在等你，用电路的原话写着，旁边有两位数字。你是靠**从
   同一块屏幕上读出那两位数字**来批准的，这就是把你这次批准绑死在这一次调用上的办法。
   同一份清单会送到桌上的 Cardputer 和 Telegram 里。

## 唯一值得抄走的那个想法

一条规则有多硬，只取决于**谁能满足它**。

`request` 和 `intent` 是 agent 自己写的。靠它们成立的规则 —— *放行前要连续四拍 intent* ——
是**一笔 agent 永远可以选择付的成本**。`blocked` 和 `confirm` 必须来自 agent 写不到的地方：
工具运行层、一个人、一条签过名的数据源。靠这些成立的规则才是**边界**。

市面上大部分 agent 护栏，是前一种穿着后一种的衣服。软件没法证明来的人是谁，所以这个项目做了
次好的事：**把这条分界画在接口形状上**。`POST /api/request` 只带 agent 自己的位。
`POST /api/tool` 带的是它不该有的位，而且要人的令牌。**一个让模型能写自己 `confirm` 的部署，
拿到的是一笔伪装成边界的成本** —— 这份 README 宁愿现在说清楚，也不想让你以后自己发现。

## 能编译的规则

| 规则 | 它拦住什么 | 靠谁成立 |
| --- | --- | --- |
| `forbid_when_blocked` | blocked 为高时什么都不放行 | 人 |
| `sticky_block` | 一拍 blocked 就停机，直到一次 confirm 解除 | 人 |
| `confirm_per_irreversible` | 不可逆的调用要自己的批准，而且用掉它 | 人 + 工具层 |
| `confirm_window_ticks` | 放行要求最近 n 拍内有过批准 | 人 |
| `two_key` | 每次放行都要两个人 | 两个人 |
| `heartbeat_ticks` | 连续 n 拍没有心跳就停机 | 工具层 |
| `trip_after_failures` | 连续 n 次失败跳闸，直到人复位 | 工具层 |
| `trip_after_refusals` | 连续 n 次被拒就停机，而不是让它一直撞边界 | 电路自己的判决 |
| `min_gap_ticks` | 两次放行之间至少隔 n 拍 | agent 改不了的东西 |
| `max_grants` | 一共最多放行 n 次 | agent 改不了的东西 |
| `commit_ticks` | 放行前要连续 n 拍 intent | **agent 自己 —— 这是成本，不是边界** |

"绝不"不是一个很大的数字。一个类设成**整类拒绝**，编译出来的电路里根本没有通往放行的路径，
后台也会用这句话告诉你。

## 钥匙在谁手上

* **页面** —— 六个视图，中英双语，默认只在你这台机器上。走 Wi-Fi 时需要操作者令牌，配对
  二维码把它放在 URL 的 fragment 里：fragment 永远不会发给服务器，页面拿到后立刻把它从
  地址栏清掉。
* **桌上的 Cardputer** —— 待确认清单显示在一台实体设备上，按下实体回车键才写下那次批准。
  走 USB，或者走 Wi-Fi（配对时设备显示四位数字，你把它敲进去）。设备只会向外轮询和上报一次
  按键，从不监听。它的令牌和那根线一样大：可以批准一个正在等、且数字对得上的调用，可以
  block —— 但**永远不能 unblock**。
* **Telegram** —— 列在 `TOOL_LAYER_CHATS` 里的聊天才算人的，批准、拦下、全停只在那里有效。
  **你的 agent 能打字进去的聊天绝不能列进来**，理由就是这个项目的全部理由。

## 链上那一半，以及实话

* 每一个决定都能在 **BNB 智能链上只读复算** —— 同一份 netlist 交给公共节点跑一遍，
  和本地判决对上或对不上。**什么都没部署**，也没有钱包可以拿来部署。
* 你的边界可以发布成一份 **ERC-8004 边界清单**，任何人都能抓下来复核，打分只有 100 或 0 ——
  边界这件事没有部分得分。
* 软件层的强制，止于你的 agent 找到另一条路。结构性的答案是电路仓库里的 **`ReflexModule`**
  （一个 Safe module，见 `docs/ONCHAIN-SELF-DEPLOY.md`）：边界没放行的交易，执行不了。
  它归你自己部署。**我们不持有任何钥匙，也不会替你部署。**

## 它不做什么

* **它不执行任何动作。** 放行只是一个判决。被允许的动作具体做什么、由谁做，都在这个程序之外。
* **它不持有钱包和私钥。** 不签名，不广播。
* **它拦不住一个同机 agent 绕到它没点到名的那条路上。** 钩子和代理会直接拒绝那些直接的尝试
  —— 往人的端点写、改规则文件或钩子配置、杀掉后台进程 —— 但那是按模式匹配的**纵深防御，
  不是防线本身**。防线是操作者令牌；结构性的答案是把 agent 跑在它根本碰不到后台端口和这些
  文件的地方：另一个系统用户、容器、禁掉本机网络的沙箱。`docs/ISOLATION.md` 就是这么做的，
  而且实测过。
* **"不可逆"是按工具名判断的。** 那是工具层的承诺，不是电路的证明。一个叫 `helper` 却会
  发邮件的工具，就是这一层的漏点。
* **拍不是时间。** 冷却八拍是八次调用，不是八秒。这里没有任何规则绑在墙上的时钟上。
* **它自己不增加任何安全性质。** 它强制的一切都来自编译出来的规则和它们的证明。电路仓库的
  `docs/AGENT.md` 写了这些证明给什么，以及更有用的：不给什么。

## 这些电路是从哪来的

这一族电路不是为这个项目发明的。它最早是**一只果蝇的逃逸反射弧** —— 一小段完整、真实的
接线，从 MaleCNS 连接组（CC-BY）重建出来，再用 NAND 门重搭一遍，好让它能被**穷尽核对**，
而不是被拿来争论。那部分工作在电路仓库里：一个静态、可审计、既不能花钱也不能存东西的东西。

这个仓库是**故意分开**的，也是**故意本地优先**的。它有进程、有端口，你要是把中继打开，
还会有一个串口设备和一个令牌。把两者分开，就是重点。

## 目录

```
console.py           服务本体：每类一颗电路、判决、待确认、清单
static/index.html    页面（总览 / 边界 / 待确认 / 流水 / Agents / 接入）
adapters/            Claude Code 钩子、MCP 代理、BNB SDK 钱包、工具分类
cardputer_relay.py   Cardputer 当确认键和停止键，走 USB
bot_telegram.py      Telegram：任何聊天可以问；批准、拦下、全停只在人的聊天里
c3s_cli/             c3s 命令、launchd 常驻、配对二维码
examples/            一个假的工作场景，和用真 agent 跑出来的记录
docs/                指南、接口、隔离、安全、规划
design/              页面所依据的、已拍板的设计画布
```

给人和给程序员的完整走法：**[docs/GUIDE.md](docs/GUIDE.md)**。
