# 原生执行轨迹：记录实际发生的调用

`bip_runtime/native_tracing.py` 在显式包裹的执行边界生成 OTLP JSON，不自动执行工具，也不会自动覆盖整个产品。日志能帮助检查数据流与调用关系；日志存在不等于输入事实正确、工程结论获证或通过赛事官方校验。

## 默认演示实际做了什么

```bash
python -m examples.native_trace
```

主例依次运行三个真实的公开接口，生成一个根 span 和三个工具子 span：

1. `compile_intake`：把合成养护描述解析为构件、病害、量测和“不出报告”等约束。它采用离线规则路径，不调用模型。
2. `evidence.retrieve`：用上一步解析出的构件和病害查询显式注入的合成图谱，返回对应条款。
3. `safe_delivery`：把检索条款加入合成候选文案，再与预先提供的合成诊断结果核对；删除错误的“主梁风险评分为30”，保留有对应证据的80及待复核状态。根 span 的最终输出就是该接口实际交付的文本。

条款 `JTG 0000-2099`、分值和候选文案均为合成夹具，**没有运行规范评分，也没有让模型生成文案**。候选中的分值不是从裂缝尺寸计算出来的。另一个真实算术拒绝例记录除零失败，用于检查异常状态，不冒充桥梁业务计算。

示例写入临时目录后重新读取 OTLP，检查父子 ID、实际调用区间、返回值和最终答复；结束时删除临时目录。即使已设置 `ENGINEERING_AGENT_TRACE_DIR`，这个演示也会在运行期间使用自己的临时目录，不在指定目录留下文件。标准输出只显示检查摘要及导入时的词表加载提示，不打印完整轨迹内容。

## 工具声明与异常记录

- `agent_trace(..., tool_definitions=[...])` 接收本次实际提供的工具定义；省略时为空。声明只是目录，实际执行仍需 `with trace.child(...):` 包裹真实函数调用。
- `REFERENCE_ONLY_TOOL_DEFINITIONS` 及兼容别名 `TOOL_DEFINITIONS` 是原应用接口参考，不能据此声称公开包已提供这些工具；不会默认写入日志。
- 异常离开子 span 时，子 span 记录错误类型、状态和实际结束时间；继续抛到根上下文时，根也记录失败。若调用者在根内部捕获异常，需要显式 `trace.mark_error(error)`，不能静默把失败记为成功。
- `safe_delivery` 扣留文本或要求复核属于业务核验结果，不一定是执行异常；应在实际结果字段里保留 `status`、`released` 和 `needs_review`，不要只看 span 成功状态。
- `record_tool_calls(...)` 兼容导入已有记录，计时覆盖的是**记录动作**，不是原工具执行时长。要测实际调用，用子 span 包裹实际代码。
- 本例没有 `chat` span。根 span 使用 `gen_ai.agent.name` 标记代理，不设置模型请求/响应字段，也不把静态代理政策写成实际模型系统提示。

## 把文件保留到自己指定的目录

不要调用会自动清理的演示函数来保存日志。先将环境变量设为自己选定、获准写入的目录，再直接使用记录器。例如：

```bash
export ENGINEERING_AGENT_TRACE_DIR="/absolute/path/to/my-synthetic-traces"
```

以下是可执行的纯合成接入代码，仍只运行上述三个工具。它保留文件，不读取生产配置或档案：

```python
import os
from datetime import date
from pathlib import Path

from bip_runtime.native_tracing import agent_trace
from engineering_agent.evidence import safe_delivery
from engineering_agent.nlp import compile_intake
from examples.native_trace import SYNTHETIC_QUERY, WORKFLOW_TOOLS
from examples.reverse_verification import synthetic_evidence, synthetic_results

# 必须由调用者显式指定；不要将生产日志目录用于公开演示。
directory = Path(os.environ["ENGINEERING_AGENT_TRACE_DIR"]).expanduser().resolve()
os.environ["ENGINEERING_AGENT_TRACE_DIR"] = str(directory)
evidence = synthetic_evidence()

def call(trace, name, arguments, function):
    with trace.child(f"execute_tool {name}", "execute_tool", {
        "gen_ai.tool.name": name,
        "gen_ai.tool.type": "function",
        "gen_ai.tool.call.id": f"{trace.trace_id}-{name}",
        "gen_ai.tool.call.arguments": arguments,
    }) as span:
        result = function()
        span.set_attribute("gen_ai.tool.call.result", result)
        return result

with agent_trace(
    [{"role": "user", "parts": [{"type": "text", "content": SYNTHETIC_QUERY}]}],
    conversation_id="synthetic-retained-trace",
    target="dynamic",
    tool_definitions=WORKFLOW_TOOLS,
) as trace:
    intake = call(trace, "compile_intake", {"text": SYNTHETIC_QUERY},
                  lambda: compile_intake(SYNTHETIC_QUERY, current_date=date(2026, 1, 1)))
    item = intake["semantically_eligible_items"][0]
    arguments = {"component": item["comp"], "disease": item["disease"]}
    clauses = call(trace, "evidence.retrieve", arguments,
                   lambda: evidence.retrieve(**arguments))
    rows = [row for row in synthetic_results()
            if row["构件"] == item["comp"] and row["病害"] == item["disease"]]
    draft = ("主梁的风险评分为30。主梁的风险评分为80。"
             f"依据 {clauses[0]['reg']} {clauses[0]['clause']}，应复核记录。")
    arguments = {"draft": draft, "results": rows, "original_query": SYNTHETIC_QUERY}
    delivered = call(trace, "safe_delivery", arguments,
                     lambda: safe_delivery(**arguments, verifier=evidence.verifier))
    trace.finish(delivered["text"])

print(directory / "traces-dynamic.json")
```

`target="dynamic"` 写入 `traces-dynamic.json`，`target="static"` 写入 `traces.json`。这些名字只区分记录目标，不表示已运行比赛 Runner。相同文件会追加后续轨迹；不要让多个进程共用同一文件。`reset_trace_file` 会重建指定目标文件，调用前应自行确认已有日志可被覆盖。

## 数据与验证边界

输入、工具参数、结果、最终答复以及异常消息都可能进入日志；记录器**不自动脱敏**。真实接入时应先确认数据授权、屏蔽密钥与个人信息、限制目录权限，并制定保留和删除策略。对外分享前检查文件内容，不要仅凭示例是合成的就认定自己保存的日志也可公开。

本仓库测试验证的是覆盖到的结构、数据流和失败行为，不是官方校验器的通过声明。接入某个赛事、平台或 Collector 时，仍须按其当前协议另行检查。
