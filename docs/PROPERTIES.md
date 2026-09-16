# Temporal properties of the escape core

Exhaustive equivalence (see [CIRCUITS](CIRCUITS.md)) says a circuit matches its
specification row by row. It does not say what the specification *guarantees over
time*. This page states five temporal properties of the escape core and proves each
one twice, with independent engines, plus a negative control per property so that a
proof cannot pass by being vacuous.

Scope: the proofs are about the committed netlist bytes of `core-hand-abc`
(`netlist_sha256` 7c8eaf62…2b52), which `core-table-abc` matches byte for byte.
`core-hand-abc-irr` has the same step relation on all 8,388,608 rows, so all five
transfer to it; `core-reach` behaves identically from reset, so P1, P2 and P5
transfer, while the all-rows form of P3 and P4 was proven only for the two circuits
above. Nothing here is a claim about the biology: these are properties of the
circuit, given the calibrated teacher's `wing_raise_ticks` = 4 (WING) and
`refractory_ticks` = 7 (REFR).

Motor codes: 0 hold, 1 raising wings, 2 short-mode takeoff, 3 long-mode takeoff.
State: a 3-bit wing-raise counter, then a 3-bit refractory timer, LSB first.

## The properties

| | Statement | Proven by |
| --- | --- | --- |
| **P1** | after a takeoff tick, no takeoff occurs for the next REFR = 7 ticks | trace search from reset; induction depth 8 |
| **P2** | a long-mode takeoff is immediately preceded by WING = 4 consecutive raising ticks | trace search from reset; induction depth 4 |
| **P3** | when not standing, or while the refractory timer runs, the core holds and the wing-raise counter is cleared for the next tick | all 8,388,608 rows; induction depth 1 |
| **P4** | a short-mode takeoff happens only with the giant-fiber pathway asserted and fewer than WING raising ticks accumulated | all 8,388,608 rows; induction depth 1 |
| **P5** | every state satisfies `raise = 0` or (`refr = 0` and `raise ≤ WING`) — the invariant that carves the 12 reachable states out of 64 | all 8,388,608 rows; induction depth 1 |

## Two methods

**Method A — the repository's own evaluator** (`scripts/check_properties.py`,
gating in CI). P3, P4 and P5 are checked on every row of the step relation, so they
hold from any state, reachable or not; for P5 that row-wise check is a one-step
induction and the reset state satisfies the invariant, so it holds forever. P1 and
P2 are properties of traces, not of single rows: a breadth-first search from reset
over (core state, monitor state) configurations evaluates all 131,072 input
patterns at each of the 12 configurations it reaches — 1,572,864 rows, no
counterexamples.

**Method B — Yosys temporal induction** (informational in CI). `formal/loom-escape.sv`
states the same five properties in SystemVerilog, one module per property. The
committed netlist is read as BLIF, with the six latch bits exported as ports
(a formal tool cannot reach inside a BLIF module) and the standalone
`policy-hand-abc` netlist instantiated alongside the core to supply `gf` — which
also cross-checks the policy inlined in the core against the separately committed
policy circuit. Each property is proven on its own, with **no assumptions**: no
property leans on another.

```sh
python scripts/check_properties.py --controls          # both methods and all controls
python scripts/check_properties.py --method yosys      # Yosys only
```

Yosys 0.68+post (git sha1 c12172fb), `sat -tempinduct -prove-asserts -set-init-zero
-verify`. The script refuses to run if WING and REFR in the spec disagree with
`wing_raise_ticks` and `refractory_ticks` in `circuits/loom-escape/decision-table.json`,
so the spec cannot drift away from the calibrated teacher.

## Negative controls

Each property is mutated into a neighbouring claim that must fail. All ten
mutations do fail, in both methods.

| Control | Mutation | Method A counterexamples |
| --- | --- | ---: |
| P1 | REFR + 1 (8 blocked ticks) | 39,526 |
| P2 | WING + 1 (5 raising ticks) | 47,089 |
| P3 | "an active, non-refractory tick never holds" | 147,576 |
| P4 | "no raises accumulated" instead of "fewer than WING" | 158,104 |
| P5 | `raise < WING` instead of `raise ≤ WING` | 7,563 |

## What went wrong first, and what it shows

P1 failed in both methods on the first run. The fault was in the property, not the
circuit: the monitor set its "ticks since takeoff" counter to 0 *on* the takeoff
tick, so the earliest legal next takeoff — 8 ticks later, once the timer has
counted 7 down to 0 — read 7 and tripped the assertion. The circuit blocks exactly
7 ticks. Both engines failed identically and both agreed once the monitor counted
from 1, which is the point of proving each property twice: a disagreement between
the methods would have pointed at the tooling, and their agreement pointed at the
specification.

## Beyond the brute-force ceiling

Everything above, and every compiled policy in `c3s/policy.py`, is checked by
enumerating the whole domain: `2^(inputs + state)` rows, about a second and a gigabyte
at 24 bits on the machine used here, four gigabytes at 26. That is the reason the
policy document recommends one circuit per tool class — the classes measured there are
9 to 15 bits, so the sentence "every row was checked" stays literally true.

If a policy ever has to exceed that, the plan is written down now so the wording does
not drift when it happens:

* **Per block, plus glue.** The latches of a compiled policy already fall into blocks
  (cooldown, streak, budget, window, halt, token, breaker, keys) whose next state reads
  only their own latches, the inputs, and `grant`. Each block can be checked on its own
  domain with `grant` as a free input, the AND that combines them checked over all its
  rows, and the partition itself checked mechanically from the netlist's cones. The
  earned sentence is then "every row of each block, and every row of the glue" — not
  "every row of the whole circuit". Note that the blocks are not independent of one
  another: `grant` is the AND of every condition and reloads the cooldown and the
  budget, so a per-block claim is sound only because `grant` enters the next state and
  never the same tick's condition.
* **Per monitor.** `properties()` walks the product of the circuit with all its
  monitors at once. Monitors observe and never feed back, so each can be walked with
  the circuit alone; the joint walk is only needed for a property that mentions two
  monitors.
* **A certificate, when enumeration is gone.** For a rule that genuinely couples
  blocks, equivalence becomes a SAT problem on a miter of the circuit and a second
  encoding of the reference, with the solver's proof archived and re-checked by an
  independent checker, and each temporal rule an IC3 or k-induction proof with a
  witness a third party can re-run. The earned sentence is "proven for all rows
  without enumerating them, by solver X, proof hash Y, checker Z" — and the encoder
  that produced the problem is named as trusted, because the proof covers the problem
  it was given and nothing before that.

None of this is implemented, and none of it is needed for any circuit in this
repository. It is here so that the day a claim has to change shape, the shape is
already chosen and the old sentence is not quietly kept.

## 中文摘要

穷尽等价只能说明"电路逐行等于规格"，说不出"随时间演化会保证什么"。这里给逃逸
核心写了五条时序性质，每条都用两套独立引擎各证一次，并且每条都配一个必须失败的
反向对照（防止"证明"其实是空证）：

* **P1** 起飞后接下来 7 拍不会再起飞；
* **P2** 长模式起飞前紧接着有 4 拍连续抬翅；
* **P3** 不站立、或处在不应期时，输出必为 hold，且抬翅计数下一拍归零；
* **P4** 短模式起飞只发生在巨纤维通路成立且抬翅计数小于 4 时；
* **P5** 任何状态都满足 `raise = 0` 或（`refr = 0` 且 `raise ≤ 4`）——正是这条不变式
  把 64 个状态里的 12 个可达态圈出来。

方法 A 是本仓库自己的位切片求值器：P3/P4/P5 在全部 8,388,608 行上逐行检查（连不可达
状态也覆盖），P1/P2 是轨迹性质，改成从复位出发在（电路状态 × 监视器状态）上做广度
搜索，12 个配置各跑 131,072 种输入，共 1,572,864 行，无反例。方法 B 是 Yosys 时序
归纳：`formal/loom-escape.sv` 用 SystemVerilog 重写同样五条，读入提交的网表字节，
每条独立证明、**不加任何假设**；归纳深度分别是 8、4、1、1、1。十个反向对照全部如预期
失败。

一个值得记下的插曲：P1 第一次在两套方法里同时失败，问题在性质而不在电路——监视器
把"距上次起飞的拍数"在起飞那一拍记成 0，于是最早合法的下一次起飞（8 拍后）读到 7，
触发断言。电路实际封锁的正好是 7 拍。两套方法同时失败、又同时通过，这正是"每条证
两遍"的用处：两者不一致会指向工具，两者一致则指向规格本身。

**超过穷举上限怎么办。** 这里的一切、以及 `c3s/policy.py` 编译出的每个策略，都是把整个
定义域 `2^(输入+状态)` 行枚举一遍核出来的——本机 24 位约 1 秒 1 GB，26 位 4 GB。这正是
策略文档建议"一类工具一张电路"的原因：量到的几类是 9–15 位，"每一行都核过"这句话字面
成立。真有一天要超过，方案已经写在上面英文一节：按块各自穷举加胶合逻辑全行核（措辞改成
"每块每行"，并说明各块通过 `grant` 耦合）；监视器逐个与电路做乘积搜索；真耦合时改用
SAT 证明加独立复核器（措辞改成"未枚举而证明，求解器 X、证明哈希 Y、复核器 Z，编码器
列为信任项"）。目前都没实现，也都不需要——写下来是为了到那天措辞跟着变，而不是悄悄沿用
旧句子。
