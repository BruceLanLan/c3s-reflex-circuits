# C3S Circuit Agent

**把「我的 agent 绝对不能做什么」写下来。规则编译成一颗电路，电路被穷尽证明，然后你的模型
每一次工具调用，都要先过这颗电路。**

不是提示词，也不是一句模型能被说服绕过去的 system message。是一颗 NAND/锁存器电路：在它
输入定义域的**每一行**上算过，在它**每一个可达状态**里证明过每一条规则——然后在你的模型每次
动手之前被问一次。你自己的模型通过适配器接进来；confirm 和 blocked 两把钥匙在人手上；每一个
判决都能在 BNB 智能链上只读复算。后台不持有钱包、不持有私钥、不执行任何动作。**放行是一个
判决，不是一次执行。**

[English](README.md) · [使用指南](docs/GUIDE.md) · [接口](docs/API.md) · [安全](SECURITY.md) · [通道](docs/CHANNELS.md)

---

## 它成立的时候是什么样

给 `files` 这一类装一条规则——*不可逆的动作要人点头，一次点头只管一次动作*——后台回给你的是
这个。不是"我保证有用"，是核对本身（节选：完整回复里还有 netlist 字节、输入名，以及逐条规则的
违例计数，全是零）：

```json
{
  "rules": ["nothing is granted while blocked is high",
            "an irreversible grant needs its own confirm, and spends it"],
  "circuit": { "nand": 18, "latch": 1, "bytes": 130, "depth": 11 },
  "checked": { "rows": 64, "matches_reference": true,
               "reachable_states": 2, "states_possible": 2,
               "rows_proven": 256, "every_rule_holds": true },
  "compiled_in_ms": 1
}
```

64 行就是它输入定义域的全部：没有哪种输入是这颗电路没见过的。256 行被证明，是每条规则在它
到得了的每一个状态里都验过一遍。`every_rule_holds: true` 不是"测试通过"，是**没有反例**。

然后你的 agent 要删东西，而钥匙在人手上。下面是对着一个后台实录下来的，不是编的：

```
第 1 拍   files   rm -rf ./build     拒绝 —— irreversible, and no unspent confirm
          → 待确认页显示这次调用，旁边是匹配码 71。人在那里批准了它。
第 2 拍   files   rm -rf build/      拒绝 —— irreversible, and no unspent confirm
                                     confirm_waiting_for: ["… rm -rf ./build"]
第 3 拍   files   rm -rf ./build     放行
第 4 拍   files   rm -rf ./build     拒绝 —— irreversible, and no unspent confirm
```

第 2 拍是模型重试时换了个写法：批准没有跟着它走，回复里点名了它正在等的那一次调用。第 4 拍才是
重点：那次批准被人读过、批过的**那一次**调用花掉了，没有变成一张长期通行证。每一条回复还都带着
`"chain": {"chain_id": 56, "agrees": true, "deployed": false}`——BNB 智能链上的一个公共节点用同一份
netlist 算了一遍，结论一致。

## 唯一值得带走的那个想法

一条规则有多硬，只取决于**谁能满足它**。

`request` 和 `intent` 是 agent 自己写的。靠它们成立的规则——*放行前要连续四拍 intent*——是**一笔
agent 永远可以选择付的成本**。`blocked` 和 `confirm` 必须来自 agent 写不到的地方：一个人、工具
运行层、桌上的一台设备。靠这些成立的规则才是**边界**。

市面上大部分 agent 护栏，是前一种穿着后一种的衣服。软件没法证明来的人是谁，所以这个项目做了
次好的事：**把这条分界画在接口形状上**。`POST /api/request` 只带 agent 自己的位。`POST /api/tool`
带的是它不该有的位，其中人的那几位要操作者令牌。一个让模型能写自己 `confirm` 的部署，拿到的是
一笔伪装成边界的成本——这份 README 宁愿现在说清楚，也不想让你以后自己发现。

## 装上

两个仓库：电路仓（编译器和它的证明）和这个后台。需要 Python 3.10 以上的 `python3`；有 `uv` 或
`pipx` 就用，没有就建一个普通 venv。

```sh
git clone https://github.com/BruceLanLan/c3s-reflex-circuits ~/work/c3s-reflex
git clone <后台仓库> reflex-console && cd reflex-console   # 尚未公开发布；从交给你的地方克隆
sh install.sh
```

`install.sh` 把 `c3s` 命令装进一个隔离环境（不碰系统 Python，不用 `sudo`），记住这个 checkout，
然后启动后台。页面在 http://127.0.0.1:8765。电路仓不在 `~/work/c3s-reflex` 就先设 `C3S_REPO`；
装完找不到 `c3s`，是 `~/.local/bin` 不在 `PATH` 里。安装只为拉那两个 Python 包上一次网；后台自己
只在做链上复算时联网，没网也照常工作。

首次启动会打印一个**操作者令牌**，同时存在 `~/.c3s-circuit-agent/operator-token`（权限 600）。
它是人的钥匙：装规则、写人的那几位（`confirm`、`confirm_b`、`blocked`、`heartbeat`）都要它。页面
只问一次，只存在那个浏览器里。**你模型的适配器永远拿不到它**——这是"能连上后台的 agent 也没法
自己批准自己"唯一的依据。能读到那个文件的 agent 就可以；所以把 agent 跑在读不到的地方
（`docs/ISOLATION.md`）。

```sh
c3s demo           # 一个假的邮箱、日历、文件夹，一件差事，每次调用都过电路——什么都不会离开这台机器
c3s status         # 在不在跑、有什么在等人
c3s stop-all       # 立刻拦下后台认识的所有 agent；--resume 解除
c3s up --lan       # 同一个 Wi-Fi 下手机也能打开——先读下面"钥匙在谁手上"
c3s pair           # 给手机扫的二维码
c3s down
```

## 三步

1. **边界（Boundaries）**——给每类工具挑一个模板，或者自己配规则。*绝不转钱*（`spend`，整类
   拒绝）、*删东西要人点头*（`files`）、*发消息要人点头*（`message`）、*连续失败三次就停*
   （`exec`）、*停机 + 心跳*（`halt`，所有类共享）。编译，看证据面板——门数、核对了多少行、每条
   规则是否成立——再安装。
2. **接入（Connect）**——把你本来就在用的模型接进来：
   * **Claude Code**——`adapters/claude_code_hook.py` 作为 PreToolUse 钩子，再加 PostToolUse 钩子
     让失败也能进电路。Bash、Write、Edit 和所有 MCP 工具都走它。
   * **任何 MCP 客户端**——`adapters/mcp_proxy.py -- <你的 MCP 服务器>` 挡在一个服务器前面。用
     `--gate` 点名要管的调用，读操作原样放过；不给 `--gate`，每次调用都是一拍。
   * **BNB Agent SDK**——把钱包包进 `adapters.bnbagent_boundary.BoundaryWalletProvider`。`spend`
     电路不放行，就签不出去。
3. **待确认（Approvals）**——什么在等你，用电路的原话写着，旁边有两位数字。页面上的按钮自带这两位
   数；从聊天里批要自己把它打出来。这两位数把你的批准绑死在这一次调用上——同一份清单会送到桌上的
   Cardputer 和 Telegram 里。

## 能编译的规则

电路库（`c3s/policy.py`）有十一条规则。这个后台能装其中十条，最后一行说了为什么。

| 规则 | 它拦住什么 | 靠谁成立 |
| --- | --- | --- |
| `forbid_when_blocked` | `blocked` 为高时什么都不放行 | 人 |
| `sticky_block` | 一拍 blocked 就停机，直到一次 confirm 解除 | 人 |
| `confirm_per_irreversible` | 不可逆的调用要自己的批准，而且用掉它 | 人，加上判断"哪些调用不可逆"的工具层 |
| `confirm_window_ticks` | 放行要求最近 n 拍内有过批准 | 人 |
| `two_key` | 每次放行都要 `confirm` 和 `confirm_b`，两把都用掉 | 两把钥匙——都用人的令牌写；后台证明不了它们在两个人手里 |
| `heartbeat_ticks` | 连续 n 拍没有心跳就停机 | 人的令牌，或桌上那台设备的在位 |
| `trip_after_failures` | 连续 n 次失败跳闸，直到人复位 | 工具层，上报框架自己的执行结果 |
| `min_gap_ticks` | 两次放行之间至少隔 n 拍 | agent 改不了的东西 |
| `max_grants` | 一共最多放行 n 次 | agent 改不了的东西 |
| `commit_ticks` | 放行前要连续 n 拍 intent | **agent 自己——这是成本，不是边界** |
| `trip_after_refusals` | 连续 n 次被拒就停机，而不是让它一直撞边界 | 电路自己的判决——**库里已编译并证明，后台目前装不了** |

"绝不"不是一个很大的数字：`max_grants=0` 是**不限次**。一个类设成**整类拒绝**，编译出来的根本
不是电路——没有任何通往放行的路径——后台也会用这句话告诉你。

## 钥匙在谁手上

* **页面**——七个视图（总览、边界、待确认、任务、流水、Agents、接入），中英双语，默认只在你这台
  机器上。`c3s up --lan` 是自选项，也正是下面这条暴露的来源。配对二维码把操作者令牌放在 URL 的
  fragment 里，fragment 不会发给服务器，页面拿到后会从地址栏清掉——这一段是干净的。但页面接下来
  **每两秒轮询一次都把令牌放在请求头里明文发出去**，因为走局域网时不带它什么都读不到。只要 `--lan`
  开着，就把操作者令牌当作同一网络上任何人都读得到的东西。`c3s pair --no-token` 只是不把它放进
  二维码，对轮询没有任何改变；只有不用 `--lan`，或者你信这个网络，才算解决。今天的补救是手机用完
  就 `c3s token rotate`。给手机单独一个只读令牌正在做，**还没有**（`docs/REDTEAM-2026-09-17.md`
  的 F1）。
* **桌上的 Cardputer**——待确认清单显示在实体屏幕上，按实体回车键才写下批准。走 USB 时中继在后台
  进程里跑，设备在位就是心跳。走 Wi-Fi 时用设备显示、你敲入的四位数配对，设备只向外轮询、只上报
  一次按键，而且它的令牌**严格比那根线弱**：能批准一个正在等的调用（带那次调用的匹配码），能
  block——不能 unblock，不能伪造心跳（它的轮询本身就是心跳），也不能全停或全部恢复。
* **Telegram**——列在 `TOOL_LAYER_CHATS` 里的聊天才算人的，`/confirm`、`/block`、`/stop` 和按钮只在
  那里有效；按一下带的就是那一次调用和它的两位数。**你的 agent 能打字进去的聊天绝不能列进来**，
  理由就是这个项目的全部理由。`docs/CHANNELS.md` 写了每条通道证明了什么、证明不了什么——没有一条
  能证明对面是人；每一条证明的都是"写入来自 agent 进不去的那一层"。

## 链上那一半，以及实话

* 每一个判决都能在 **BNB 智能链上只读复算**：同一份 netlist 交给公共节点，用 `eth_call` 加状态覆盖
  跑一遍，和本地判决对上或对不上，结果写在回复里。**什么都没部署**，也没有钱包可以拿来部署。
* 你的边界可以发布成一份 **ERC-8004 边界清单**，任何人都能抓下来复核，打分只有 100 或 0——边界这
  件事没有部分得分。生成它的 `c3s/erc8004.py` 在电路仓的 `boundary` 分支上，**那个分支还没发布**；
  对着已发布的 `main` 跑的后台没有清单这一项。
* 软件层的强制，止于你的 agent 找到另一条路。结构性的答案是 **`ReflexModule`**——同一条未发布分支
  上的一个 Safe module：边界没放行的交易，执行不了。它归你自己部署。**我们不持有任何钥匙，也不会
  替你部署。**

## 它不做什么

* **它不执行任何动作。** 放行只是一个判决。被允许的动作具体做什么、由谁做，都在这个程序之外。
* **它不持有钱包和私钥。** 不签名，不广播。
* **它拦不住一个同机 agent 绕到它没点到名的那条路上。** 钩子和代理会直接拒绝那些直接的尝试——往人
  的端点写、改规则文件或钩子配置、杀掉后台进程——但那是按模式匹配的，而且匹配的是默认端口和字面
  写法：**纵深防御，不是防线本身**。防线是操作者令牌，而令牌只在 agent 读不到它的时候管用。把 agent
  跑在碰不到后台端口和这些文件的地方：另一个系统用户、容器、禁掉本机网络的沙箱。`docs/ISOLATION.md`
  就是这么做的，而且写了怎么验证。
* **它证明不了任何一条通道对面是人。** 页面证明按键的人拿着令牌文件；Cardputer 证明有人在那台设备
  旁边；聊天证明消息来自白名单里的会话。每一条都是边界——只要 agent 没有路进到那一层。
* **红键只管它认识的名字。** 按下之后才第一次出现的 agent 不在拦下的名单里，一个换名字的 agent 能
  从红键底下走出去。页面会写"m 个里拦下了 n 个"，再按一次会追上。`REFLEX_REQUIRE_AGENT_TOKEN=1`
  能堵住换名，但今天还没有命令行的绑名字路径（见指南）。
* **"不可逆"是按工具名和参数判断的。** 那是工具层的承诺，不是电路的证明。一个叫 `helper` 却会发
  邮件的工具，就是这一层的漏点。
* **拍不是时间。** 冷却八拍是八次调用，不是八秒。这里没有任何规则绑在墙上的时钟上。
* **它自己不增加任何安全性质。** 它强制的一切都来自编译出来的规则和它们的证明。电路仓的
  `docs/AGENT.md` 写了这些证明给什么，以及更有用的：不给什么。

## 这些电路是从哪来的

这一族电路不是为这个项目发明的。它最早是**一只果蝇的逃逸反射弧**——一小段完整、真实的接线，从
MaleCNS 连接组（CC-BY）重建出来，再用 NAND 门重搭一遍，好让它能被**穷尽核对**而不是被拿来争论：
173 个门，全部 8,388,608 行。那部分工作在电路仓里：一个静态、可审计、既不能花钱也不能存东西的东西。

这个仓库是**故意分开**的，也是**故意本地优先**的。它有进程、有端口，你要是把中继打开，还会有一个
串口设备和一个令牌。把两者分开，就是重点。

## 目录

```
console.py           服务本体：每类一颗电路、判决、待确认、任务、清单
static/index.html    页面（总览 / 边界 / 待确认 / 任务 / 流水 / Agents / 接入）
adapters/            Claude Code 钩子、MCP 代理、BNB SDK 钱包、工具分类、微信中继
cardputer_relay.py   Cardputer 当确认键和停止键，走 USB 或 Wi-Fi
bot_telegram.py      Telegram：任何聊天可以问；批准、拦下、全停只在人的聊天里
c3s_cli/             c3s 命令、launchd 常驻、配对二维码
menubar/             macOS 菜单栏（可选 extra）
examples/            c3s demo 背后那个假的工作场景，和用真 agent 跑出来的一次记录
docker-compose.yml   把 agent 关进一个到不了后台的容器（docs/ISOLATION.md）
install.sh           一条命令的安装
docs/                指南、接口、安装、通道、隔离、红队报告
design/              页面所依据的、已拍板的设计画布
```

给装它的人和改它的人的完整走法：**[docs/GUIDE.md](docs/GUIDE.md)**。
