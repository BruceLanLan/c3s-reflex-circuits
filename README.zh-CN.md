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
| **对照实验** | <!-- RESULT:readme-zh-controls --> |
| **密封测试族** | <!-- RESULT:readme-zh-sealed --> |
| **可复现性** | 两次从零构建逐字节一致；`scripts/verify.sh` 重新检查全部内容 |

## 证据阶梯

每一层回答不同的问题，任何一层都不能冒充另一层。

| 层 | 问题 | 位置 |
| --- | --- | --- |
| **L0** 生物学来源 | 用了哪些细胞、哪些突触、哪些发布文件？ | [docs/CONNECTOME.md](docs/CONNECTOME.md)、`data/` |
| **L1** 可执行教师 | 什么方程、什么参数、每个参数从哪来？ | [docs/TEACHER.md](docs/TEACHER.md)、`c3s/loom.py` |
| **L2** 硬电路 | 哪些门、多少个、多深？ | [docs/CIRCUITS.md](docs/CIRCUITS.md)、`circuits/` |
| **L3** 等价性 | 每个电路是否就是它的规格？ | [docs/CIRCUITS.md](docs/CIRCUITS.md#how-equivalence-is-established) |
| **L4** 公共机器 | 任何人能否在 EVM 上重放？ | `contracts/` |

行为保真度、对照实验与密封测试见 [docs/EVALUATION.md](docs/EVALUATION.md)；简化、假设与不主张的内容见 [docs/LIMITATIONS.md](docs/LIMITATIONS.md)。详细文档以英文为准。

## 工作原理

1. **连接组（L0）。** 从固定哈希的 MaleCNS v1.0 发布文件中，提取 LC4 与 LPLC2 对两根巨纤维（`DNp01`）以及一组候选“并行”下行神经元的输入。连接组提供的是每条通路里“逼近速度”与“逼近大小”两种驱动的**比例**：投到巨纤维约 1.2–1.4 : 1，投到并行候选约 3.8–4.1 : 1。
2. **教师模型（L1）。** 每条通路的驱动 = 线性速度项 + 高斯大小项（Ache 等 2019 报告的形式），按上述突触数加权。翅膀抬起之前巨纤维先过阈值 → 短模式起飞；否则由并行通路驱动长模式程序（von Reyn 等 2014 的时序机制）。三个自由参数在训练刺激族上，按文献导出的约束做网格标定。
3. **编码。** 每只眼把角大小与扩张速度各量化为 4 位对数分箱：共 16 位输入，即 **LoomEscape-16**。
4. **电路（L2）。** 可读的手写策略（每只眼的阈值阶梯）、ABC 优化版本、可微逻辑门网络学习得到的电路，以及用 6 个 LATCH 加上运动状态机的带状态核心。
5. **证明（L3）。** 每个电路都用位切片求值器在完整定义域上检查；ABC 被视为不可信的优化器；两个独立的 Python 求值器与 EVM 求值器必须一致。
6. **上链（L4）。** `NandMachine` 可以求值任何 TapeOut 字节布局的网表；`ReflexCore` 装载一个逃逸核心，每个调用者拥有独立的 LATCH 状态，没有管理员。

## 复现

需要：Python ≥ 3.10（`numpy`、`torch`、`pytest`；重新抽取连接组还需要 `pyarrow`、`pandas`），Yosys（提供 `yosys-abc`），Foundry。

```sh
pip install -e ".[dev,learn,connectome]"
scripts/verify.sh          # 测试、完整重建、逐字节核对产物、EVM 测试
scripts/verify.sh --full   # 另外重新下载并抽取 MaleCNS 子图（约 1.1 GB）
```

## 研究基础

* **连接组。** MaleCNS v1.0（Berg 等，*Cell* 2026），由 HHMI Janelia 的 FlyEM 团队与剑桥大学、MRC 分子生物学实验室以及 **Google Research** 共同完成；自动分割使用了 Google Research 的 flood-filling networks。
* **逃逸回路。** Ache 等 2019（LC4 速度输入与 LPLC2 大小输入）；von Reyn 等 2014（放电时序选择起飞模式）与 2017（线性特征整合）。
* **逻辑学习。** 深度可微逻辑门网络（Petersen、Borgelt、Kuehne、Deussen，NeurIPS 2022，arXiv:2210.08277），本仓库独立重新实现；以及 **Google Research** 的可微逻辑元胞自动机（Miotti、Niklasson、Randazzo、Mordvintsev，2025），首次在带状态的循环电路中训练这类逻辑门。
* **边界。** Scheffer 与 Meinertzhagen 2021（《连接组是不够的》）；Pospisil 等 2024（以连接组为先验估计因果模型）。

## 许可与声明

代码：Apache-2.0。连接组聚合数据来自 CC-BY 数据集，使用时须署名。DLGN 方法的参考实现声明了“Patent pending”。详见 [NOTICE](NOTICE)。
