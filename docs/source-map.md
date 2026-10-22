# 技术组合与代码来源

本仓库是从桥梁工程应用中抽取的机制代码，加上为公开复现而新增的隔离适配器。它不是原生产产品的完整镜像，也不是将一个数学推理演示重新命名为桥梁系统。

这里的“原实现”指来自原应用代码，不表示未经修改、已获得全面安全认证或每个入口均已在生产中启用。抽取时会调整导入、删除私有配置耦合，并按测试结果修复公开副本。文件级来源见 [source-provenance.json](source-provenance.json)；公开副本修复应同时记录在相应机制文档中。

## 四类状态

| 标记 | 含义 |
| --- | --- |
| 原实现抽取 | 从原工程保留的实现，可有导入路径或配置隔离调整。是否覆盖某个场景仍由测试决定。 |
| 公开版新适配 | 本次为无私有数据、可复现演示或明确输入输出而新增的连接层。不是原生产实现已经具备的同名能力。 |
| 接口 / 基础组件 | 契约、扩展点、简单默认实现或尚需调用者提供数据的组件。存在类名不等于完整能力已接入。 |
| 未随仓库提供 | 原产品页面、真实资料或不支持的操作；不能根据相关代码名称推断已实现。 |

## 组合顺序：模型提出候选，工程机制决定如何采信

```text
用户描述 / 调用者提供的报告
        │
        ├─ 自然语言：语义候选 + 对象 / 时间 / 任务 / 约束
        └─ 报告表格：规则解析 + 原文 / 行列 / 文档来源
        ↓
结构化候选与证据合同（未知、估计、冲突仍保留）
        ↓
残差状态：目标、已有证据、缺失条件、待解决项
        ↓
检索 / 图谱 / 规则 / 模型提出下一步候选
        ↓
验证闸门 → 接受、拒绝、暂缓或中断
        ↓ 仅接受的候选进入受控状态更新
确定性计算 / 状态迁移 / 有限不变量检查
        ↓
候选答复 → 数字、数量、构件和引用反向核验
        ↓
受限制的输出 + 残余问题 + 审计记录

记忆为后续候选提供可审核的上下文；不能绕过本轮证据与验证。
检查点记录过程；它不是任意外部副作用的撤销按钮。
```

这张图说明机制的职责和连接方式，不表示任何输入都会自动经过全部模块。具体运行路径取决于公开示例、调用者配置和领域适配。

## 逐项代码映射

下表路径均相对本仓库根目录；保留原路径是为了便于审查源头，不表示仍需运行原 Web 应用。

| 技术环节 | 原实现抽取 | 公开连接方式及边界 |
| --- | --- | --- |
| 口语预处理与病害字段解析 | 原 `legacy_v7/app.py` 中的纯解析函数，抽入 `engineering_agent/nlp.py`、`query_normalizer.py` | **公开版新适配**隔离原客户端、缓存和运行配置。规则归一化与模型语义理解应区分；解析到字段不等于现场事实确证。 |
| 自然语言任务结构 | `bip_runtime/semantic_frame.py`、`semantic_frame_v2.py`、`semantic_llm_parser.py` | 保留对象、时间、意图、约束等语义结构；模型由调用者显式注入，离线执行不声称调用了模型。 |
| 报告表格解析 | `legacy_v7/core/report_parser_v2.py` | `engineering_agent/report_input.py` 是**公开版新适配**：为支持的 DOCX 表格绑定文档哈希、表/行/列和原文。不是任意 PDF、扫描件、版式或 OCR 的通用解析器。 |
| 领域词汇归一化 | `legacy_v7/core/ontology.py` | 桥梁构件、病害别名及分类的代码适配；其他行业必须提供自己的词汇与规则，不能直接套用桥梁判断。 |
| 证据能力与范围约束 | `bip_runtime/evidence_capabilities.py`、`archive_scope.py`、`diagnosis_scope.py`、`claim_grounding.py` | 限定对象、年份、指标与来源；`claim_grounding` 仍使用桥梁 ontology，不是完全领域无关的解析器。 |
| 残差、候选与验证闸门 | `nskernel/dialogue/reasoning/residual_state.py`、`candidate_and_transition.py`、`verifier_gate.py`、`extended_checkers.py`、`loop_manager.py` | 候选接受前做已配置检查；残差表示未解决条件。它不是“所有逻辑均被形式化证明”的声明。 |
| 桥梁推理接入 | `domain/reasoning_bridge.py`、`divergence_controller.py` | 连接残差控制、证据能力、数值核验、记忆和蒸馏的实际桥梁适配层；不能用单独的通用内核替代这层并声称完成了桥梁业务。 |
| 权威检索与图谱 | `legacy_v7/core/agents.py` 的 `RegulationRAGAgent`、`knowledge_graph.py` 的节点/边、检索和路径解释 | `engineering_agent/evidence.py` 为**公开版新适配**：显式注入内存条款与图关系，调用原 `retrieve`、`find_clauses` 和核验逻辑，不自动搜索私有数据目录。已提供合成示例与负例测试，见 [reverse-rag.md](reverse-rag.md)。 |
| 反向 RAG / 输出核验 | `legacy_v7/core/claim_verifier.py`、`bip_runtime/claim_grounding.py`、`nskernel/verification/numeric_claims.py` | 不是再问一个模型“是否正确”，而是把可机械核验的输出主张同本轮结果、数量、数值、构件和条款依据核对。检索到内容仍不等于证据适用，缺少权威数据时不能宣称已完成同等强度核验。 |
| 确定性评分与双轨审计 | `legacy_v7/core/scoring_agent.py`、`score_governance.py`、`report_score_extractor.py`、`repair_report_audit.py` | 保留计算和报告原值/系统校核值的来源区别。规则数据未捆绑；`scoring_config.py` 仅为公开隔离配置，空目录或默认行为不是有效工程规则库。 |
| 记忆提取、匹配与持久审核 | `bip_runtime/euler_memory.py`、`legacy_v7/core/state_store.py`；桥梁接入见 `domain/reasoning_bridge.py` | 发布算法和空库结构，不发布历史会话、已批准记忆或用户信息。记忆匹配与审核状态不是当前事实正确的充分证据。 |
| 受控表述 | `nskernel/dialogue/reasoning/strict_orchestrator.py` | 基于已接受事实组装内容、可选风格化、再次核验及回退的组件；验证强度仍取决于 checker 与输入证据。 |
| 状态回退、检查点与任务重试 | `nskernel/reasoning/controller/`、`bip_runtime/jobs.py`、`legacy_v7/core/state_store.py` | 详细区别见 [checkpoints-and-recovery.md](checkpoints-and-recovery.md)。公开示例新增故障注入测试；不是外部工单、网络调用或支付的通用撤销。 |
| 可观测性与输入边界 | `bip_runtime/native_tracing.py`、`nskernel/trace/tracer.py`、`bip_runtime/*guard.py`、`nskernel/guardrails/` | 发布记录和检查逻辑，不附原生运行日志。日志可能包含调用者输入，接入方需要另行设置脱敏与保留策略。 |

公开版 `native_tracing` 默认工具目录为空；调用者须显式传入本次实际提供的 `tool_definitions`。历史原应用目录保留为 `REFERENCE_ONLY_TOOL_DEFINITIONS`，不是可调用工具，也不会默认附在日志中。
新增 `python -m examples.native_trace` 把真实 `compile_intake → evidence.retrieve → safe_delivery` 调用放在各自 span 区间内：解析结果决定检索对象，图谱召回条款进入合成候选文案，核验删除错配数值后，根 span 记录实际交付文本。向临时目录写 OTLP JSON 后重新读取，断言父子关系、时间区间和实际结果；另用真实算术拒绝例检查错误状态。示例不调用模型，不制造 `chat` span，结束后清理临时文件，只打印验证摘要。条款、待核验分值和候选文案均为显式合成输入；分值不是由该例的解析结果计算出来的，不能把此例说成完整规范评分或模型运行。
`record_tool_calls` 是导入调用者已有记录的接口，生成的时间只覆盖记录动作；不能拿它事后创建的 span 冒充原始工具耗时。

## 不能由目录名推断的能力

- `nskernel/contracts/` 定义数据与领域契约；接口存在不表示环境分析、因果推断等领域能力已拥有有效数据并完成部署。
- `nskernel/knowledge/authority_rag/base.py` 是内存索引与简单关键词检索的基础实现，不是完整桥梁图谱或独立事实裁判。
- 通用 proposer、LLM client、calculator 和验证器扩展点，需要实际实现、配置和测试。未接入模型时不能把离线规则处理写成“模型推理”。
- `FormulaDistiller.distill()` 当前返回空列表，是公式提炼的占位，不具备自动发现并证明新公式的能力。普通步骤模板蒸馏与公式发现是不同功能。
- `NumericalSamplingChecker` 未注入 sampler 时只做结构检查；当前默认不能证明表达式数值等价。`SymbolicEquivalenceChecker` 只检查候选声明的 `equivalence_verified` 标志，不会独立调用符号证明器。
- `OperationRuleChecker` 当前读取候选的 `violated_rules`，不独立发现任意运算违例。上述扩展点不在本节合成桥梁默认验证组合中，不能因类名存在就宣传为已部署的数学证明或工程认证。
- 本仓库不包含 `reasoner_kernel` 数学实验工程或 `opensource/residual-agent`；不以 Lean、定理证明榜单或形式化引理组合替代对本桥梁代码的验证。
- 原产品完整 Web UI、移动采集端、真实档案检索、企业用户服务、供应商模型账号与生产部署，不作为本仓库的完整功能承诺。

## 审查一个能力时看什么

按“输入 → 代码调用 → 验证决定 → 最终状态/输出 → 测试断言”追踪，而不是按目录数量或术语数量判断。

尤其要区分：模型生成候选与系统接受候选、条款召回与条款适用、解析成功与事实确证、记忆命中与证据成立、记录检查点与恢复执行、拒绝提交与撤销已提交操作。

公开演示与测试只证明所覆盖的合成情形。任何性能、效率、准确率或现场适用性的对外结论，都应附单独的数据来源、评测口径和复现条件。
