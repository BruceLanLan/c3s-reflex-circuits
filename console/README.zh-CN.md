# C3S Circuit Agent — 后台

C3S Circuit Agent 的本机后台：你写好 agent 必须遵守的规则，规则编译成 NAND/锁存器电路，在定义域每一行上核对、在每个可达状态上证明；你自己的 AI 模型通过适配器接进来，它的每一次工具调用都由这颗电路放行或拒绝。要人拍板的事，你可以在网页上、桌上的 Cardputer 上、或 Telegram 里处理。[English](README.md)

## 从这里开始

```sh
export C3S_REPO=~/work/c3s-reflex                 # 电路仓库（提供 c3s.policy）
python console.py                                 # 打开 http://127.0.0.1:8765
REFLEX_CARDPUTER=1 python console.py              # 同上，并把 USB 上的 Cardputer 当确认键
```

1. **边界（Boundaries）**：每类工具选一个模板——**不能转钱**（spend 整类拒绝）、**删东西要人点头**（files）、**发消息要人点头**（message）、**连续失败就停**（exec）、**停机+心跳**（所有类共享）。编译并安装后，证据面板会显示门数、逐行核对的行数、每条规则是否成立。
2. **接入（Connect）**：把你的模型接进来：
   * Claude Code：`adapters/claude_code_hook.py` 作为 PreToolUse 钩子（再加一个 PostToolUse 钩子上报失败），Bash、Write、Edit 和所有 MCP 工具都会经过它。
   * 任何 MCP 客户端：`adapters/mcp_proxy.py -- <你的 MCP 服务器>`，只管会改东西的调用，读操作直接放行。
   * BNBAgent SDK（BNB Agent Studio）：把钱包包进 `adapters/bnbagent_boundary.BoundaryWalletProvider`，spend 电路不放行，就签不了任何交易。
3. **待确认（Approvals）**：等人拍板的请求，拒绝理由是电路的原话。**一次确认只对那一次调用有效。** 同一份清单也在 Cardputer（菜单 3）和 Telegram 里（`bot_telegram.py`，只有列在 `TOOL_LAYER_CHATS` 里的人的聊天能批）。
4. **流水（Activity）**：每一个决定、电路读到的输入位，以及在 BNB 智能链上的只读复算（什么都不部署）。
5. **公开（Publish）**：接入页第 4 步生成 ERC-8004 边界清单，任何人都能复核并打分（`c3s-reflex/scripts/validate_boundary.py`）；链上的 Safe 模块由你自己部署（`c3s-reflex/docs/ONCHAIN-SELF-DEPLOY.md`）。

用真实 agent 处理假邮箱的完整记录见 `examples/README.md`。

## 它保证什么，不保证什么

* 钩子和代理会**直接拒绝 agent 篡改边界本身**：用 curl/python 调后台的 `/api/tool`、`/api/policy` 给自己批准或改规则，改规则文件或钩子配置，杀掉后台进程。这是按调用内容的模式判断，挡得住直接的尝试，挡不住绕路；结构上的办法是让 agent 跑在碰不到后台端口和这些文件的地方（另一个系统用户、容器、禁止访问本机网络的沙箱）。
* 规则管住的是**经过适配器的那些调用**。模型如果有别的路（另一个 shell、没接代理的服务器、浏览器），这里管不到；链上的 Safe 模块形态才是结构性的。
* "不可逆"是按工具名判断的，是工具层的承诺，不是电路的证明。
* 拍不是时间：冷却 8 拍是 8 次调用，不是 8 秒。
* 后台不持有钱包和私钥，不签名、不广播、不替你执行任何动作。
