# C3S 反射弧电路

**受连接组约束的电路综合：从果蝇巨纤维逃逸通路，到经过穷举验证、可以在链上运行的 NAND/LATCH 网表。**

[English](README.md)

这个仓库只问一个很窄的问题：**如果一个行为是由实测的神经连线塑造的，最少需要多小的一台确定机器才能复现它？这台机器有多少部分可以被证明，而不是被信任？**

它不把大脑搬上链。它只取一条反射——由巨纤维（Giant Fiber, GF）把关的逼近物逃逸——沿着五层证据一路走到底，每一层都对照上一层检查。

```
MaleCNS v1.0 连接组 ─► 显式教师模型 ─► 16 位决策表
     （实测）          （引用 + 假设）     （65,536 行）
                                              │
       EVM / TapeOut 字节布局网表 ◄─ NAND+LATCH 核心 ◄─┘
          （公开、可重放）            （穷举等价）
```

本页是面向开发者的中文指引：怎么跑起来、代码在哪、想改某样东西该动哪里。方法细节与全部数字以英文文档为准，下文会直接链过去。

## 在线演示

[`docs/demo/index.html`](docs/demo/index.html) 是一个方块世界里的逃逸反射实验台：向果蝇发射一个逼近的方块，页面把每只眼看到的角大小和扩张速度量化成 16 个输入位，在浏览器里逐个单元求值 173 NAND + 6 LATCH 的核心网表，每 5 ms 一拍显示通路指示灯、锁存器和运动指令，并在结束时与连续教师模型的结果对照。

* 本地打开：`python -m http.server -d docs 8000`，然后访问 <http://localhost:8000/demo/>。
* GitHub Pages 开启（`main` 分支、`/docs` 目录）后，地址是 `https://brucelanlan.github.io/c3s-reflex-circuits/demo/`。
* 页面每次加载都会核对嵌入网表的 SHA-256，并回放 Python 构建写入的参考回合；不一致会直接显示在页面上。
* 页面里的 JavaScript 求值器已在完整定义域上与 Python 求值器、tapeout.net 公开求值器逐字节对照（见 [docs/CIRCUITS.md](docs/CIRCUITS.md#in-browser-evaluator-docsdemo)）。

## 结果一览

| | |
| --- | --- |
| **实测连线** | 在 MaleCNS v1.0 中，LC4 + LPLC2 提供了左右两根巨纤维**视觉投射神经元输入**突触的 99.86 %（左）和 99.61 %（右）——但只占它们**全部输入**突触的 25.8 % |
| **策略电路** | 16 位感觉输入 → 2 位通路输出，**74 个 NAND**，深度 11，在全部 65,536 个输入上与教师模型完全一致 |
| **带状态的逃逸核心** | **173 NAND + 6 LATCH**（1,235 字节）；单拍转移关系在全部 8,388,608 个（输入, 状态）组合上与规格一致 |
| **与连续教师模型的行为对照** | 逃不逃一致率 1.00；短/长模式一致率 0.83（训练族）与 0.90（留出族）；起飞时刻误差约 1 拍（5 ms） |
| **EVM** | 18 个电路的全域差分测试通过；核心每一拍在参考求值器上约 37 万 gas |
| **TapeOut 字节布局** | 与 tapeout.net 公开的解码器和求值器交叉验证：4,800 个随机拍与最终电路均 0 差异 |
| **学习得到的电路（DLGN）** | 行准确率 67–90 %，216–1,340 个 NAND：在可以完整列表的函数上，精确综合胜出 |
| **对照实验** | 把实测突触数错配到其他位置的 23 种排列里，18 种找不到任何满足约束的教师参数；能标定成功的 5 种全都保留了“并行通路以 LC4 输入为主”这一项——连线确实约束行为，但只通过一个序关系 |
| **密封测试族** | 事先以 SHA-256 登记、只评估一次：48 个未见刺激上逃不逃一致率 1.00，模式一致率 0.875 |
| **可复现性** | 两次从零构建逐字节一致；`scripts/verify.sh` 重建教师标定、决策表、电路、元件、演示页数据与 EVM 测试数据并逐字节核对（DLGN、对照实验与密封评估需单独运行；DLGN 重跑后网表完全一致） |

## 五分钟上手

只想读代码、跑测试、玩电路，不需要 Yosys、Foundry，也不需要下载连接组：

```sh
git clone https://github.com/BruceLanLan/c3s-reflex-circuits.git
cd c3s-reflex-circuits
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,learn]"
pytest -q                                  # 50 个测试，含穷举等价检查
```

在 Python 里拿起一个已提交的电路，喂一帧刺激：

```python
import json
from c3s import exhaust, loom
from c3s.netlist import from_bytes

m = json.load(open("circuits/loom-escape/policy-hand-abc.json"))
c = from_bytes(bytes.fromhex(m["tapeout_netlist_hex"][2:]), len(m["inputs"]), len(m["outputs"]))
print(c.metrics())                          # NAND 数、LATCH 数、深度、字节数

outs, _ = exhaust.step_table(c)             # 全部 65,536 行的输出表
x = loom.encode_features(40.0, 900.0, -20.0)  # 张角 40°、扩张 900°/s、左前方 20°
print("巨纤维", outs[x] & 1, "并行通路", outs[x] >> 1 & 1)
```

完整复现（重建所有产物、逐字节核对、跑 EVM 测试）需要 Yosys 0.68（提供 `yosys-abc`）和 Foundry 1.8.1。已提交的产物是用 Yosys 0.68 与 CPU 版 torch 2.14 构建的；换了 ABC 版本可能综合出不同但等价的网表，此时穷举等价检查照常通过，只有逐字节核对会失败。每次推送，`.github/workflows/verify.yml` 会在干净机器上跑测试、逐字节重建 EVM 测试数据与演示数据，并跑 Foundry 全域差分测试。

```sh
pip install -e ".[dev,learn,connectome]"
scripts/verify.sh          # 测试、完整重建、逐字节核对产物、EVM 测试
scripts/verify.sh --full   # 另外重新下载并抽取 MaleCNS 子图（约 1.1 GB）
```

## 目录地图

| 路径 | 内容 | 什么时候看 |
| --- | --- | --- |
| `c3s/netlist.py` | 网表 IR、TapeOut 字节编解码、标量单拍求值器、`Builder`（带记忆化的 NAND 构造器） | 想手写电路、读写网表字节 |
| `c3s/exhaust.py` | 位切片穷举求值器：`step_table`、`truth_table`、`assert_equivalent` | 想证明两个电路等价 |
| `c3s/components.py` | 可复用元件（比较器、加法、popcount、argmax、计数器、不应期门）和 `CATALOG` | 想加一个新元件 |
| `c3s/connectome.py` | 从固定哈希的 MaleCNS 发布文件抽取逃逸子图 | 想换细胞类型或核对突触数 |
| `c3s/loom.py` | 逼近几何、编码 `Encoding`、教师模型 `TeacherParams`、决策表、回合模拟 | 想改编码或教师方程 |
| `c3s/calibrate.py` | 教师参数网格标定与约束 C1–C3 | 想改标定目标 |
| `c3s/reflex.py` | 手写策略电路、带状态的逃逸核心、核心回合模拟 | 想改运动状态机 |
| `c3s/synth.py` | ABC 桥接（不可信优化器，结果回读后重新穷举验证） | 想换综合配方 |
| `c3s/dlgn.py` | 可微逻辑门网络的独立实现，训练后硬化为网表 | 想做学习电路 |
| `scripts/` | 各阶段入口脚本与 `verify.sh` | 想重建某一层 |
| `circuits/` | 每个电路一个 JSON 清单：指标、SHA-256、网表字节、证据 | 想直接用电路 |
| `contracts/` | `NandMachine`、`ReflexCore`（Solidity）与 Foundry 测试 | 想上链重放 |
| `data/` | 连接组聚合数据（源数据 CC-BY） | 想核对生物学来源 |
| `docs/` | 英文详细文档；`docs/demo/` 是网页演示 | 想看方法与局限 |
| `tests/` | pytest 测试 | 改完代码先跑 |

## 核心概念

**网表格式。** 信号 0 恒为 0，信号 1 恒为 1，信号 2 起是输入，之后每个单元产生一个信号；最后 `nOut` 个信号是输出。

| 单元 | 字节 | 语义 |
| --- | --- | --- |
| `NAND a b` | `0x00 ‖ u24 a ‖ u24 b` | `¬(a ∧ b)`，`a`、`b` 必须是更早的信号 |
| `LATCH d` | `0x01 ‖ u24 d` | 输出上一拍结束时存下的位（初始为 0），本拍结束时存入信号 `d`；`d` 可以指向后面 |

输入与状态都是低位在前打包的整数。完整说明与 TapeOut 兼容性见 [docs/CIRCUITS.md](docs/CIRCUITS.md#netlist-format)。

**LoomEscape-16 编码。** 每只眼两个 4 位对数分箱：角大小（10°–180°）与扩张速度（25–12,800 °/s）。输入位顺序是 `size_L, speed_L, size_R, speed_R`。分箱 0 表示低于第一条边界，最高分箱吸收超出部分。每只眼看自己一侧再加越过中线的 30°。

**策略与核心。** 策略电路（16 → 2）只回答“这一拍巨纤维和并行通路是否过阈值”；核心（17 → 2，多一个 `standing` 输入）在策略之上加运动状态机：`motor` 为 0 静止、1 抬翅中、2 短模式起飞、3 长模式起飞；6 个 LATCH 是 3 位抬翅计数器加 3 位不应期计时器。

**证据阶梯。** 每一层回答不同的问题，任何一层都不能冒充另一层。

| 层 | 问题 | 位置 |
| --- | --- | --- |
| **L0** 生物学来源 | 用了哪些细胞、哪些突触、哪些发布文件？ | [docs/CONNECTOME.md](docs/CONNECTOME.md)、`data/` |
| **L1** 可执行教师 | 什么方程、什么参数、每个参数从哪来？ | [docs/TEACHER.md](docs/TEACHER.md)、`c3s/loom.py` |
| **L2** 硬电路 | 哪些门、多少个、多深？ | [docs/CIRCUITS.md](docs/CIRCUITS.md)、`circuits/` |
| **L3** 等价性 | 每个电路是否就是它的规格？ | [docs/CIRCUITS.md](docs/CIRCUITS.md#how-equivalence-is-established) |
| **L4** 公共机器 | 任何人能否在 EVM 上重放？ | `contracts/` |

行为保真度、对照实验与密封测试见 [docs/EVALUATION.md](docs/EVALUATION.md)；简化、假设与不主张的内容见 [docs/LIMITATIONS.md](docs/LIMITATIONS.md)。

## 常见开发任务

### 加一个新元件

1. 在 `c3s/components.py` 里用 `Builder` 写构造函数，再写一个 `_make_xxx()`，返回 `Component(name, summary, inputs, outputs, n_state, build, reference)`。`reference(x, s)` 是 Python 参考模型，返回 `(输出, 下一状态)`；组合电路用 `_comb(fn)` 包一层即可。现有的 `_make_counter`、`_make_refractory` 是时序元件的范例。
2. 加进 `CATALOG`。`tests/test_components.py` 会自动在完整（输入, 状态）定义域上逐位核对电路与参考模型。
3. `python scripts/export_components.py` 写出 `circuits/components/<name>.json`；`python scripts/export_evm_fixtures.py` 会自动把 `CATALOG` 里的元件纳入 EVM 全域差分测试，再 `(cd contracts && forge test)` 复核。

### 改编码

1. 在 `c3s/loom.py` 里新建一个 `Encoding`（名字、位数、两组边界），加进 `scripts/compare_encodings.py` 的 `CANDIDATES`。
2. `python scripts/compare_encodings.py` 只在训练族上比较候选编码；`DEFAULT_ENCODING` 必须与脚本的选择规则一致，否则脚本直接报错。这是为了让编码选择保持“事先规则、事后不挑”。
3. `python scripts/build_loom_escape.py` 重建决策表、策略与核心。位数变了，输入数、核心大小和 EVM 测试数据都会跟着变。

### 改教师参数或方程

* 方程在 `c3s/loom.py` 的 `drives`、`select_action`、`core_step`；每个参数的出处写在 [docs/TEACHER.md](docs/TEACHER.md)。
* 标定网格与约束在 `c3s/calibrate.py`。`python scripts/build_loom_escape.py --params <某个 teacher-calibration.json>` 可以跳过网格搜索，直接复用一组参数。
* 改完后 `scripts/verify.sh` 会因为产物变化而在逐字节核对处失败——这是预期的：确认新数字合理后提交新产物。

### 训练学习电路（DLGN）

```sh
python scripts/train_dlgn.py --widths 128,128,64 --epochs 200 --seed 0
```

结果写到 `circuits/loom-escape/dlgn-128x128x64-s0.json`（`--tag` 可改名），包含硬化后的网表、准确率与核心回合。硬化网表同样走穷举检查；它和决策表的差异就是报告里的行准确率。

### 用到链上或 TapeOut

* 每个清单里的 `tapeout_netlist_hex` 就是 TapeOut 字节布局的网表，输入输出数在 `inputs`、`outputs`。
* `contracts/src/NandMachine.sol` 能求值任意这种布局的网表；`contracts/src/ReflexCore.sol` 部署时固定一个核心，每个调用者拥有独立的 LATCH 状态，没有管理员。用法以 `contracts/test/` 为准。
* 字节布局与单拍语义已与 tapeout.net 公开求值器对照；工厂函数签名只在其前端代码中观察到，费用、链上大小限制与部署行为**没有**核验。本仓库没有向任何链部署过电路。

### 更新网页演示

重建电路后运行 `python scripts/build_demo.py`，它把核心网表、编码、教师参数和参考回合写进 `docs/demo/index.html` 的数据块；`scripts/verify.sh` 会检查这一步也能逐字节复现。页面本身是单个 HTML 文件，界面代码直接改这个文件。

## 常见问题

**为什么不直接训练一个神经网络上链？** 这个函数只有 16 位输入，可以完整列表。精确综合给出 74 个 NAND 且可证明等价；DLGN 在同一个函数上用 216–1,340 个 NAND 只达到 67–90 % 行准确率。

**连接组到底约束了什么？** 对照实验表明：把突触数错配到其他位置，大多数情况下找不到满足文献约束的参数；能成立的排列都保留了“并行通路以 LC4 为主”这一个序关系。详见 [docs/EVALUATION.md](docs/EVALUATION.md)。

**这是果蝇大脑吗？** 不是。这是一条反射的最小确定性模型，简化与不主张的内容列在 [docs/LIMITATIONS.md](docs/LIMITATIONS.md)。

**`verify.sh` 没跑哪些东西？** DLGN 训练、对照实验（约 25 分钟）和密封评估（需要尚未公开的密封刺激规格）需要单独运行。

## 研究基础

* **连接组。** MaleCNS v1.0（Berg 等，*Cell* 2026），由 HHMI Janelia 的 FlyEM 团队与剑桥大学、MRC 分子生物学实验室以及 **Google Research** 共同完成；自动分割使用了 Google Research 的 flood-filling networks。
* **逃逸回路。** Ache 等 2019（LC4 速度输入与 LPLC2 大小输入）；von Reyn 等 2014（放电时序选择起飞模式）与 2017（线性特征整合）。
* **逻辑学习。** 深度可微逻辑门网络（Petersen、Borgelt、Kuehne、Deussen，NeurIPS 2022，arXiv:2210.08277），本仓库独立重新实现；以及 **Google Research** 的可微逻辑元胞自动机（Miotti、Niklasson、Randazzo、Mordvintsev，2025），首次在带状态的循环电路中训练这类逻辑门。
* **边界。** Scheffer 与 Meinertzhagen 2021（《连接组是不够的》）；Pospisil 等 2024（以连接组为先验估计因果模型）。

完整列表见 [docs/REFERENCES.md](docs/REFERENCES.md)。

## 作者与联系方式

作者：[BruceBlue](https://github.com/BruceLanLan) · 联系：X [@BruceBlue](https://x.com/BruceBlue)

## 许可与声明

代码：Apache-2.0。连接组聚合数据来自 CC-BY 数据集，使用时须署名。DLGN 方法的参考实现声明了“Patent pending”。详见 [NOTICE](NOTICE)。
