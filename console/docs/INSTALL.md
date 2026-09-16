# 装它、起它、配对手机（W1）

这一份讲三件事：怎么把 `c3s` 命令装上、后台怎么常驻、手机怎么扫码进审批页。命令一律是英文
原文，可以直接复制。

> 底线不变：这里没有任何一步会替 agent 执行动作、拿私钥、签名或广播。`c3s stop-all` 是唯一
> 会写后台的命令，它写的是"人的位"`blocked`。

---

## 0. 需要什么

| 东西 | 说明 |
| --- | --- |
| Python ≥ 3.10 | 本机的 `python` 是 Python 2，装的时候用 `python3`；`install.sh` 会自己挑一个能用的 |
| 电路仓 `c3s-reflex` | 后台用它编译并逐行核对规则（`c3s/policy.py`）。默认找 `~/work/c3s-reflex`，否则设 `C3S_REPO` |
| 值班台这个 checkout | `console.py` 与 `static/index.html` 从 checkout 里跑，**不打进 wheel**（见 §2） |
| macOS | 常驻（launchd）与菜单栏只做 macOS；Linux/Windows 用 CLI 一样能起，只是没有 launchd 与菜单栏 |

唯一的第三方依赖是 `numpy`（电路仓的 `c3s/policy.py` 要它）。串口钥匙（Cardputer）要
`pyserial`、菜单栏要 `rumps`，两者都是可选 extra：不用就不会被装下来。

## 1. 一条命令

```sh
cd ~/work/reflex-console
sh install.sh              # 装 c3s，然后 c3s up
sh install.sh --lan        # 同时绑 0.0.0.0，手机可扫码（先读 §5 再决定）
sh install.sh --service    # 同时装 launchd 常驻（重启/被杀都会回来）
sh install.sh --no-start   # 只装不起
```

`install.sh` 依次尝试 `uv tool install` → `pipx install` → `~/.c3s-circuit-agent/venv` 里的
venv，永远不装进系统 Python、永远不用 sudo。装完它把这个 checkout 的路径记到
`~/.c3s-circuit-agent/console-dir`，以后在任何目录 `c3s up` 都能起对同一个后台。

手动装：

```sh
uv tool install .                      # 或 pipx install .
uv tool install '.[menubar,cardputer]' # 要菜单栏和串口钥匙
```

改过代码再装同一个版本号时，uv 会命中缓存装成旧的，要加 `--reinstall`：

```sh
uv tool install --force --reinstall .
```

## 2. `c3s` 启的是谁

`c3s` 只是外壳：它找到 checkout，用**自己这个环境的 Python**（`sys.executable`，装 c3s 时
建的那个隔离环境，里面有 numpy）去跑 checkout 里的 `console.py`，工作目录就是 checkout。

所以 `console.py`、`static/index.html`、`adapters/`、`examples/` 都**没有**被打进包里。这是
故意的：你审批时看到的页面就是仓库里的那一份，升级是 `git pull` 而不是重装；否则 wheel 里会
留一份过期的页面，两份哪份在跑都说不清。

找 checkout 的顺序（第一个命中的算）：

1. `REFLEX_CONSOLE_DIR`
2. `~/.c3s-circuit-agent/console-dir`（`install.sh` 写的，`c3s up` 在 checkout 里跑时也会写）
3. 当前目录
4. `~/work/reflex-console`

找电路仓：`C3S_REPO`，默认 `~/work/c3s-reflex`。两者找不到时报一句话，说明该设哪个变量，
不猜、不继续。

## 3. 命令

```sh
c3s up                 # 起后台、开页面、打印操作者令牌
c3s up --lan           # 绑 0.0.0.0，并打印局域网配对二维码
c3s up --service       # 顺带装 launchd 常驻
c3s up --no-browser    # 不开浏览器（脚本里用）
c3s up --port 8766     # 换端口，可以和另一个后台并存
c3s status             # 在不在、有几件事等人、日志在哪
c3s pair               # 再打一次二维码（--ip 选网卡、--no-token 不把令牌放进 URL）
c3s token              # 看令牌；c3s token rotate 换一个新的
c3s token rotate --agent <名字>   # 换的是那个 agent 自己的令牌（I-3）：删掉绑定，它下次带令牌的请求重新绑
c3s stop-all           # 给后台认识的每个 agent 写 blocked=1（要令牌）
c3s stop-all --resume   # 解除
c3s down               # 停（--service 同时卸掉 launchd 并删 plist）
c3s demo               # 模拟工作台（W4 的 examples/workbench.py，没有就说明归谁）
c3s menubar            # 菜单栏（--print-menu 只打印菜单项不驻留）
```

`c3s up` 会自己看端口：已经有一个后台在答话就说清楚、不去抢；端口被别的东西占着就报错并建议
`--port`。`c3s down` 如果发现后台是 launchd 起的，会直接告诉你"杀了它 launchd 还会拉起来，
要 `c3s down --service`"。

`c3s status` 里"等人处理"的条数读的是后台自己算的 `pending[]`（`docs/API.md` I-2）——页面、
设备、聊天、CLI 四处读同一个答案，这里不自己再算一遍。旧后台没有这个字段时显示 `—` 并说明
原因。

## 4. 常驻（launchd）

`c3s up --service` 写 `~/Library/LaunchAgents/work.c3s.console.plist` 并
`launchctl bootstrap gui/$UID`：

- `RunAtLoad` + `KeepAlive`：登录就起、被杀就拉起来（`ThrottleInterval` 2 秒）。
- 日志 `~/.c3s-circuit-agent/console.log`（不是 `/tmp`，重启不会丢）。
- `PATH` 里带 `~/.foundry/bin`：launchd 不读 shell 配置，manifest 的 keccak256 要 `cast`。
  没有 `cast` 后台照样跑，只是哈希那一栏会说算不出来。
- **plist 里不放令牌。** plist 是 644，谁都能读；令牌留在
  `~/.c3s-circuit-agent/operator-token`（600），后台自己去读。

它是 **user agent**（`gui/<uid>`），不是 daemon：没有一步需要 root，后台必须以持有令牌与
规则的那个人的身份跑。

```sh
launchctl print gui/$(id -u)/work.c3s.console   # 状态、pid、最后退出码
launchctl kickstart -k gui/$(id -u)/work.c3s.console  # 手动重启
c3s down --service                              # 卸掉并删 plist
```

## 5. 手机配对：二维码里有什么，暴露了什么

二维码的内容就是一个 URL：

```
http://<本机局域网 IP>:8765/#approvals&token=<操作者令牌>
```

**要知道的三件事：**

1. **令牌在 URL 的 fragment（`#` 之后）里，不在 query string 里。** fragment 不会发给服务器，
   所以它进不了后台日志（`log_message` 会把整条请求行打进
   `~/.c3s-circuit-agent/console.log`）、进不了代理日志、也不会跟着 `Referer` 走——这是
   `docs/API.md` "Pairing a phone" 定的形式（`0bf4847` 取代了早先的 `?token=`）。它仍然会以明文
   经过你的局域网（后台是 HTTP，不是 HTTPS），也会短暂出现在手机地址栏里：页面读到就存进
   localStorage 并把它从地址栏移除（见 §8：这一段页面改动尚未落地）。只在你信得过的网络上扫；
   扫完不放心就 `c3s token rotate`。
2. **`--lan` = 绑 `0.0.0.0`，同一个 Wi-Fi 上的任何设备都能连这个端口。** 后台里有几个端点
   **不要令牌**：`GET /api/state`（能读到全部流水与规则）、`GET /api/manifest`、
   `POST /api/request`（能以任意 agent 名义发请求）、`POST /api/tool` 的 `irreversible` /
   `failed`。也就是说，同一网络上的人能看你的流水、也能往你的电路里灌请求（灌请求只会让裁决
   更严，不会替他放行——人的位 `confirm`/`blocked`/`heartbeat` 和装规则都要令牌）。默认
   `c3s up` 绑 `127.0.0.1`，只有本机能连；要手机审批才用 `--lan`，家里的 Wi-Fi 可以，咖啡馆
   不要。把这些端点也门住是 W6 的活（`docs/ISOLATION.md`）：协调者已派 W6 在后台绑非回环地址时
   收紧它们（`/api/request` 必须带 agent 令牌、`/api/state` 必须带其中一种令牌），到那时
   `--lan` 才算安全，而不只是"提醒过你"。在那之前这一段话就是全部的保护。
3. **Host 检查放宽到了本机的局域网地址。** 后台启动时算出本机所有网卡的 IPv4 地址
   （`console.py` 的 `_own_ipv4_addresses`），绑 `0.0.0.0` 时把 `<那些 IP>:<端口>` 也算作
   "自己的名字"，否则手机带着 `Host: 192.168.x.y:8765` 进来会被 403。这**不是**把防 DNS
   重绑定的检查拆了：别人的页面还是带着别人的域名来，不在这个集合里，照样 403。只覆盖 IPv4，
   回环与 169.254 不算；IPv6 的局域网地址不在其中（配对 URL 用 IPv4）。

不想让令牌上网络就：`c3s pair --no-token` 出一个不带令牌的二维码，手机扫进去以后手动粘贴令牌
（`c3s token` 打印它）。

`c3s token rotate` 写一个新令牌进文件，然后：launchd 起的会被 `kickstart -k` 重启；普通进程起
的要自己 `c3s down && c3s up`（后台只在启动时读一次令牌）；如果那个后台是带
`REFLEX_OPERATOR_TOKEN` 环境变量起的（`docs/DEV-v1.0-workstreams.md` §0.2 那条 nohup 命令就
是），文件换了也没用，命令会直接这么告诉你。换完：所有页面、手机、bot 都得重新给一次，旧二
维码作废。

**`c3s token rotate --agent <名字>`** 换的是另一样东西：`docs/API.md` I-3 里那个 **agent 自己
的令牌**。后台从来只存它的哈希，所以"轮换"就是**删掉绑定**（`~/.c3s-circuit-agent/agents.json`
里那一条），下一次那个 agent 带着新的 `REFLEX_AGENT_TOKEN` 发请求就重新绑上。这一条不需要后台
在跑（绑定在文件里），命令直接调 `console.py` 的 `token_rotate()`，不自己动那个文件。

## 6. 二维码是自己编的

`c3s_cli/qr.py` 是标准库写的 QR 编码器（字节模式、版本 1–10、GF(256) 上的
Reed–Solomon、八种掩码与四条罚分规则、BCH 的格式与版本信息），没有网络请求、没有额外依赖：
配对不该依赖装包。它对不对不靠自称——

```sh
python -m c3s_cli.qr_verify     # 需要 pip install qrcode；c3s 本身永远不需要
```

把版本 1–10 × 四个纠错级别 × 八种掩码（两边都强制同一掩码，把"选掩码"这一步从比较里剔掉）
逐个模块和参考实现对一遍，1600 张矩阵全等才算过。

## 7. 起得多快

`c3s up` 自己计时：从命令发出到 `GET /api/state` 回 200 的秒数会打在第二行。本机（M 系列
Mac、规则已存在）实测 **1.0 秒**，第一次要编译并逐行核对电路，慢一点。

## 8. 页面读取配对令牌（已落地，2026-09-16 收尾）

配对 URL 是 `http://<IP>:<端口>/#approvals&token=<令牌>`（`docs/API.md` 的 "Pairing a
phone"）。页面打开时从 fragment 里取出令牌、存进这台浏览器的 localStorage、并立刻把它从地址栏
移除（截图、历史记录、转发的链接里都不再有它）；之后页面读 `/api/state` 时带上它，这正是
局域网上的手机需要的。令牌仍然在本地网络上走过一次，补救是 `c3s token rotate`。

不想让令牌上网络：**`c3s pair --no-token`**，二维码里只有 `http://<IP>:<端口>/#approvals`，
扫码的手机到审批页后被问一次令牌（粘贴 `c3s token` 打印的那一串）。

## 9. 菜单栏

```sh
uv tool install --force '.[menubar]'
c3s menubar                # 驻留
c3s menubar --print-menu   # 只打印菜单项（没有图形会话时用这个自查）
```

图标是果蝇，标题后面跟等人处理的条数；菜单五项：打开后台 / pending N / 全部停下 / 重启后台 /
退出（退出的是菜单栏，不是后台）。后台不在时标题变 `🪰·`、菜单项写"console not running"，
不抛异常。菜单栏**不能**批准任何东西：`confirm` 属于页面、Cardputer 或聊天——人要在能读到内容
的地方批。

## 10. 卡住了

| 现象 | 怎么办 |
| --- | --- |
| `port 8765 is taken by pid …` | 别的后台在跑：`c3s status` 看是不是自己的，或 `c3s up --port 8766` 并存 |
| `python: command not found` / 语法错 | 本机 `python` 是 Python 2，用 `python3`，或直接用 `install.sh` |
| 改了代码但 `c3s` 行为没变 | uv 命中了缓存：`uv tool install --force --reinstall .` |
| launchd 一直重启 | 端口被占后台起不来就会循环：`c3s down`、看 `~/.c3s-circuit-agent/console.log` |
| 手机打不开页面 | 后台是不是绑在 `0.0.0.0`（`c3s status` 的 `bound to`）；`c3s pair --ip` 换网卡（VPN 的 `utun` 地址手机连不上） |
| 菜单栏没图标 | `rumps` 没装，或不在图形会话里；用 `c3s menubar --print-menu` 自查 |

## 11. 这一份里没有验证的

- 真机扫码：没有第二台设备在手，"手机扫码进审批页并批准一条"这一条**未验证**；URL 的可达性
  是用 `curl` 带 `Host: <局域网 IP>:<端口>` 验的。
- IPv6 局域网地址：不在 Host 允许列表里，也不进二维码，**未做**。
- Linux / Windows：CLI 与二维码是跨平台的写法，但只在 macOS 上跑过；launchd 与菜单栏本来就
  只做 macOS，**未验证**。
- `c3s demo`：`examples/workbench.py` 归 W4，还没有，所以只验证了"没有时说清归谁"。
