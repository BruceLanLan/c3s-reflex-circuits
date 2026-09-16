# 人这一侧的三条通道

反射弧（reflex arc）自己不放行任何东西：电路把每次工具调用判成放行或拒绝，
而 `confirm` / `blocked` 这几个位**只能由人写**。这一页讲人从哪里写、
每条通道**证明了什么**、以及**没有证明什么**。

## 一条规则

> **通道成为边界的唯一条件：写这个位的人所在的那一层，agent 没有任何路径能进去。**

不满足这条，剩下的全是戏。电路照样拒绝该拒绝的，但它等的那个 confirm
是 agent 自己能生产的——于是"审批"变成 agent 付一次就过的成本，
而不是一道它过不去的边界。

判断一条通道时只问三件事：

1. agent 拿不到写这个位需要的凭据（operator token、messenger 的密钥、设备 token）。
2. agent 打不进那个会话 / 那块屏幕（不能以人的身份发言）。
3. agent 到不了接收端（回调地址、串口、console 的监听地址）。

三条里破一条，这条通道就退化成 agent 的自有通道。

## 三条通道，各自证明什么

| 通道 | 写位时凭什么 | 证明了 | 没有证明 |
| --- | --- | --- | --- |
| **页面**（console 的 `/#approvals`） | operator token（`~/.c3s-circuit-agent/operator-token`，600），加 `Host`/`Origin` 同源检查 | 按下的人读得到 token 文件，并且请求来自 console 自己的页面 | 读得到那个文件的**不一定是人**。跑 agent 的机器如果能读它，agent 就能写自己的 confirm |
| **Cardputer**（串口或配对后的 Wi-Fi） | 串口的物理在位；Wi-Fi 是配对过的 device token + 屏幕上那两位数 | 有人在**这台设备旁边**，而且读到了屏幕上这次调用的数字；device token 的权力被砍到只剩"和线一样多" | 不证明是谁。设备被拿走就等于钥匙被拿走——这是它的门槛也是它的上限 |
| **即时通信**（Telegram、微信） | allowlist（`TOOL_LAYER_CHATS` / `WECHAT_TOOL_LAYER_USERS`）+ relay 持有的 operator token + 那两位数 | **allowlist 里的那个会话里，有谁读到了这次调用的数字**，仅此而已 | 不证明是人，也不证明不是 agent。如果 agent 能往那个会话里发言、或能拿到 messenger 的凭据，它就是"那个谁" |

三条通道**没有一条能证明对面是人**。它们证明的都是"写入来自 agent 进不去的那一层"——
这正是上面那条唯一规则，也是全部安全性的来源。

## 两位数（code）是干什么的

`GET /api/state` 的 `pending[]` 里每一项带一个两位数 `code`，绑定到
`(agent, reason, bit)`，调用一变就重发，项目一消失就作废（docs/API.md，I-2）。
它**不是密码**——泄露它没有意义。它解决的是另一个问题：

> 让"批准"必须经过**看见这次调用本身**，而不只是"在 allowlist 里"。

这是 CISA 的号码匹配（number matching）那一套：光在名单里不够，
得把屏幕上那次调用旁边的数字念出来。所以：

- 页面和 Cardputer 上 `code` 可以省——东西已经在人手里了。
- **聊天通道必须送 `code`**。console 只在收到 `code` 时校验它，
  所以"总是送"是通道自己的义务：`bot_telegram.py` 和
  `adapters/wechat_relay.py` 都无条件带上。
- `for_reason` 才是把 confirm 钉在某一次调用上的东西。两者一起送，
  一个 confirm 就既不能挪给别的调用，也不能在调用变了之后还生效。

**紧急停止是例外**：`全停` / `stop` / `blocked: 1` 不要数字。
急停不能卡在"念一个屏幕上的数字"上，而且它只会让边界更严
（docs/API.md 里 device 为什么 block 不需要 code，同一个理由）。
松的那个方向——解除拦截、`blocked: 0`、`/resume`——只能回到 console 做。

## Telegram：按钮就是那一次调用

有东西开始等人时，bot 把它连按钮一起发出来：同意这次调用 / 拦截这个 agent /
全部停下 / 先不处理。

一次按下携带的东西和手打 `/confirm` 完全一样：`for_reason` 和 `code`。
Telegram 的 `callback_data` 只有 64 字节，装不下 `reason`，所以按钮里带的是
`(agent, reason, bit)` 的摘要（12 个十六进制字符）；按下时**拿 console
当前在等的东西重算一遍摘要**来定位。于是：

- 按钮不可能表示"批准现在在等的随便哪个"——摘要对不上就什么都不写。
- 调用被新的调用顶掉了、`code` 重发了、已经有人批过了——
  按下会明说并且什么都不写。
- 旧按钮（bot 重启之前发的）拦截时会说"这按钮比我这次启动还老"，
  让人改用 `/block <agent>`。
- **按钮和手打命令受同一道门**：不在 `TOOL_LAYER_CHATS` 里的会话按下就被拒。
  agent 自己能打字的会话，永远不要写进 `TOOL_LAYER_CHATS`。

## 微信：`adapters/wechat_relay.py`，目前是模拟的

用户手上没有微信 bot 账号，这份代码也不会去要。所以它写成一个 transport 接口，
两个实现：

- `SimulatedTransport`——本地两个队列，没有网络、没有账号、没有凭据。
  测试用它，`python -m adapters.wechat_relay --sim` 也用它，
  人可以在命令行里扮演那台手机，看到 console 到底接受什么。
- `WeComTransport`——照企业微信文档写的薄适配层，`UNTESTED = True`，
  **从来没有对着真账号跑过一次**。缺凭据时它直接拒绝构造，并且不会向任何人索要。

微信没有 Telegram 那种按钮，所以人是**把数字打出来**：
`确认 47` / `二次确认 47` / `拦截 47` / `全停` / `待办`。
`code` 在所有等待项里唯一、调用变了就重发、项目没了就作废，
所以"确认 47"本身就钉住了那一次调用——和按钮一样窄。

### 一个微信部署要成立，必须满足什么

回到那条唯一规则。console 没法从一条消息看出写它的是人还是 agent，它从来也不能；
它信的是**通道**，而通道是由持有 operator token 的那个 relay 背书的。
Telegram 的答案是 `TOOL_LAYER_CHATS`——一个 agent 没有路径进去的会话。
微信这边是 `WECHAT_TOOL_LAYER_USERS`，形状一样，值也就值这个前提本身。

1. **agent 不持有 messenger 的凭据**（企业微信的 app secret，或任何能以那个身份发言的会话）。
   持有了，它就能自己写批准，allowlist 只是它付一次的成本。
2. **allowlist 里的 id 属于一个人，且在 agent 发不进去的会话里。**
   agent 往里推状态的那个群，不是这个会话。
3. **agent 读不到 operator token 文件，也到不了喂给这个 relay 的回调端点。**
   任何一条破了，它都可以绕开微信直接写。

企业微信的入站消息是**推到回调地址**的（签名 + AES 加密），不是轮询——
所以 `WeComTransport.poll()` 直接抛异常，真部署必须自己跑那个回调端点、
验签、解密，再把消息交给 `Relay.on_message(user, text)`。
**那道验签是承重的**：它就是第 3 条里"到不了回调端点"的实现。

个人微信没有官方 bot API；能谈的部署只有企业微信，这也是这里只给一个真 transport 的原因。

## 没有账号，所以没验的部分

- `WeComTransport.send()` 的取 token / 发消息两个 HTTP 调用：照文档写的，**没跑过**。
- 企业微信回调端点的验签和解密：**这个仓库里根本没有**，是部署方的活。
- 真手机上的 Telegram 按钮渲染与 `answerCallbackQuery` 的实际弹窗：
  测试里把回调流量伪造掉了，没有连过真的 Telegram API。

以上三项的状态是"未验证"，不是"应该没问题"。
