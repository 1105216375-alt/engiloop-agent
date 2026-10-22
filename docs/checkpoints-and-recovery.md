# 断点、回退与恢复：实际支持到哪一层

本文区分原应用的已接入机制、公开抽取版的实现和本轮新增的合成验证。
它不是“任意操作都能撤销”或“从任意断点无损续跑”的声明。

## 六种机制，不应混为一个“回滚”

| 机制 | 当前实现 | 实际保证与限制 |
| --- | --- | --- |
| 拒绝提交 | `nskernel/dialogue/reasoning/loop_manager.py`、`verifier_gate.py` | 候选先过验证闸门；只有 ACCEPT 才交给 MainStateUpdater。REJECT、DEFER、INTERRUPT 不把该候选写入主残差状态。它是**未提交**，不是撤销已完成操作。 |
| 内存状态回退 | `nskernel/reasoning/controller/runtime_controller.py`、`controllers.py` | 每步生成新状态后检查；发现已覆盖的无效状态则深拷贝旧状态，记录 rollback。不是通用数据库事务或外部服务事务。 |
| 持久审计检查点 | `legacy_v7/core/state_store.py` | SQLite 保存 run、step、stage、hash、payload，能够关闭后重新读取。读取不会自动重放执行；hash 当前只保存，不自动校验。 |
| 持久任务与整任务重试 | `bip_runtime/jobs.py`、StateStore 的 jobs 方法 | 审批等待、请求键去重、任务领取事务、过期租约回收、失败重试。重试重新进入 handler，**并非从最后一个业务步骤续跑**。 |
| 迟到结果阻止提交 | 原应用 `legacy_v7/core/live_demo.py` | 原应用的演示执行器让取消检查与阶段成果提交共用锁；接受取消后，后台晚到结果不再发布。该业务演示执行器不包含在本节独立示例中。它不撤销已产生的网络调用、费用或外部效果。 |
| 外部副作用补偿 | 未实现通用机制 | 没有通用 saga、补偿事务、已发送消息撤回、第三方费用撤销或文件操作的全局 undo。外部写入需要独立审批、幂等键、可补偿接口和领域适配。 |

## 真正的组合顺序

```text
准备 ResidualState（目标、缺口、约束、已有证据）
    ↓
StopPolicy 检查完成 / 失败 / 阻塞 / 预算 / 停滞
    ↓
按 priority 调用 proposer，先收集本步候选
    ↓
逐候选运行硬检查 + 软检查
    ├─ INTERRUPT → 停止本步尝试，记录后续建议
    ├─ REJECT / DEFER → 不提交该候选，尝试下一候选
    └─ 首个 ACCEPT → MainStateUpdater → ResidualRebuilder
                                    ↓
                          RollbackHandler 检查新状态
                            ├─ 无效 → 保留旧状态 + 回退事件
                            └─ 有效 → 使用新状态
                                    ↓
                         记录本步审计，进入下一轮
```

注意：本步各 proposer 会先被调用来收集候选，再执行候选验证；不是第一个被拒绝后才调用第二个 proposer。
因此 proposer 应保持为只读计算或候选生成，不能假定闸门会撤销 proposer 内部已经发生的外部写入。
候选来源名称、置信度、结构匹配和 `VerifiedFact` 类名，也都不等同于对外部世界真实性的证明；保证强度取决于实际配置的 checker 与证据。

`MainStateUpdater` 拒绝应用非 ACCEPT 决策。状态更新采用 `evolve()` 生成下一版残差。
当前 ResidualRebuilder 负责完成状态与条件性完成标记，未实现一般依赖失效图的完整自动重建。
EscalationPolicy 返回换模型、换 proposer、人工复核等建议，RuntimeController 记录建议；此处不是已自动执行升级操作。

## 本轮公开版修复：Enum 状态判断

原实现用 `str(state.status).endswith("solved")` 检测“已解决但仍有未解决项”。
Python 的 `str(ResidualStatus.SOLVED)` 是 `ResidualStatus.SOLVED`，不是小写 `solved`；导致该防线漏检。
后缀比较还会误把 `unsolved` 当作 solved。

本轮只在公开抽取副本修改为：优先读取枚举的 `.value`，再与完整字符串 `solved` 做精确比较。
这是公开版修复，不声称原生产副本已同步修复。新增回归测试覆盖枚举、普通字符串、无效状态以及实际 RuntimeController 的回退日志。

## 如何复现

在本仓库根目录、安装项目依赖后执行：

```bash
python -m examples.checkpoints_and_rollback
python -m unittest discover -s tests -p 'test_checkpoint_rollback.py' -v
```

全部输入是新构造的合成状态；不读取真实桥梁档案、模型密钥、会话记录或应用数据库。
SQLite 位于 `TemporaryDirectory`，演示结束即清理。失败重试用受控时钟前移演示，不等待真实退避时间。

演示逐项断言：

1. 硬检查拒绝候选，旧状态不变，rollback 次数为 0。
2. 候选通过闸门后，故障注入器产生“SOLVED 但仍有缺口”的状态；真实 RuntimeController 回退一次，旧状态保留。
3. SQLite 检查点关闭连接后仍可读取，但仅作为审计数据。
4. 等待审批的任务不执行；审批并释放后执行。相同幂等键返回同一任务。
5. handler 首次失败后再次从入口执行，attempt 从 1 到 2，展示整任务重试。
6. 租约过期后由替代 worker 重新领取；运行中的队列任务不会被 `cancel_job` 伪装成已取消。

示例中的故障注入器不是新增业务能力；它只用于证明防线真实影响最终状态。

## 审计重放不是业务重放

RuntimeController 会保留候选、验证决策和尝试转移。发生回退时，`summary.transitions` 中仍可能包含该次**尝试**转移，步骤中的 `to_residual_id` 也可能指向尝试的新状态。
消费审计数据时必须同时读取 rollback 事件与最终状态；不能将“闸门曾 ACCEPT”或“有一条 transition”单独计作最后已采信结果。
回退不会倒转预算计数和累计尝试统计。这些记录反映真实消耗与尝试，不应悄悄抹除。

原桥梁应用的 `domain/reasoning_bridge.py` 在推理结束时保存 `reasoning_completed` 检查点；这包含步骤摘要、事实 ID 和未知项等审计信息，不是逐步完整执行快照。
原应用报告任务会通过进度回调保存阶段 checkpoint，但重试仍重新调用整个报告 handler。
原应用的这些接入点用于说明来源，不表示其客户档案、Web 服务和报告流水线都随本独立示例发布。

## 仍需工程加固的边界

- **精确断点续跑**：需完整可序列化状态、版本化规则、工具输入输出、恢复协议和跨版本兼容。当前没有自动 checkpoint → 恢复执行接入。
- **幂等不是恰好一次**：现有幂等键用于入队去重；同一个键的不同 payload 也返回既有任务。接入方应按规范化请求生成键，并自行拒绝键与内容冲突。
- **租约不是 fencing**：当前任务完成与失败更新未校验 lease_owner，也没有持续续租心跳。长任务租约到期后旧 worker 仍可能返回，不能声称分布式恰好执行一次。
- **取消不是杀进程**：原应用演示器的取消阻止后续发布，不保证立即停止线程或供应商请求；队列 `cancel_job` 只处理尚未运行的状态。
- **状态不变量是有限集合**：当前检查覆盖空状态、缺失 residual_id、SOLVED 与未解决项冲突，不是任意数据破坏检查器。调用方应保持旧状态不被就地改写；`snapshot_before` 引用本身不是不可变防护容器。
- **外部副作用**：邮件、工单、支付、报告发布等须另设提交闸门、幂等协议和补偿操作。推理状态回退不能替代它们。

准确的对外表述是：**候选先校验，已覆盖的不一致更新可以回退，执行过程留下审计检查点，任务失败能够重新尝试；涉及外部世界的提交另受工程控制。**
