"""Optional OTLP-JSON recorder for explicitly instrumented execution boundaries.

The recorder has no OpenTelemetry SDK dependency and contains no scoring logic.
It does not execute registered tools or automatically instrument every module.
Callers explicitly supply the tools actually available to a traced request;
historical application tool schemas are reference material only.
"""

from __future__ import annotations

import contextvars
from copy import deepcopy
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Iterable


SERVICE_NAME = "engineering-domain-agent"
INSTRUMENTATION_NAME = "engineering-domain-agent.native-genai"
INSTRUMENTATION_VERSION = "1.0.0"
DEFAULT_TRACE_DIR = Path(__file__).resolve().parents[1] / "outputs" / "native_logs"

# Reference only: these schemas describe the original application's interfaces,
# not tools delivered or executable by this extracted package. Never advertise
# them implicitly in a public trace.
REFERENCE_ONLY_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "name": "bridge_diagnosis_pipeline",
        "description": "解析桥梁病害并执行规则诊断、证据核验和处治建议生成",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "type": "function",
        "name": "BridgeArchiveSearch",
        "description": "按桥名、年份、幅别和报告标识检索唯一桥梁档案或档案集合",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "requested_years": {"type": "array", "items": {"type": "string"}},
                "requested_sides": {"type": "array", "items": {"type": "string"}},
                "report_id": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "BridgeIdentityVerifier",
        "description": "核验用户请求中的桥名与档案唯一绑定结果一致，阻止跨桥证据串用",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "resolved_bridge": {"type": "string"},
                "requested_bridges": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status", "resolved_bridge"],
        },
    },
    {
        "type": "function",
        "name": "ArchiveReportPipeline",
        "description": "对已限定桥名、年份和幅别的档案报告执行真实解析、诊断和评分",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "bridge": {"type": "string"},
                "requested_years": {"type": "array", "items": {"type": "string"}},
                "requested_sides": {"type": "array", "items": {"type": "string"}},
                "reports_analyzed": {"type": "integer"},
            },
            "required": ["status", "bridge"],
        },
    },
    {
        "type": "function",
        "name": "EvidenceContractBuilder",
        "description": "把报告来源、病害事实和评分指标编译为可追溯证据合同",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "schema_version": {"type": "string"},
                "facts": {"type": "integer"},
                "reports": {"type": "integer"},
            },
            "required": ["status", "schema_version"],
        },
    },
    {
        "type": "function",
        "name": "ScoreProvenanceVerifier",
        "description": "核验报告评分和系统重算评分的来源，禁止无依据地相互覆盖",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "policy": {"type": "string"},
                "reported_scores": {"type": "integer"},
                "recomputed_scores": {"type": "integer"},
                "calibration_decision": {"type": "string"},
            },
            "required": ["status", "policy"],
        },
    },
    {
        "type": "function",
        "name": "ResidualStateBuilder",
        "description": "从目标、证据合同、约束和未知项构造初始ResidualState",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "evidence_contract_schema": {"type": "string"},
            },
            "required": ["question", "evidence_contract_schema"],
        },
    },
    {
        "type": "function",
        "name": "ResidualReasoningEngine",
        "description": "在预算和停机策略内提出候选步骤并生成ResidualState状态转移",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "initial_residual_id": {"type": "string"},
            },
            "required": ["run_id", "initial_residual_id"],
        },
    },
    {
        "type": "function",
        "name": "ResidualVerifierGate",
        "description": "逐步核验候选是否安全、可追溯且真实缩减残差，只接受通过的状态转移",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "transition_count": {"type": "integer"},
            },
            "required": ["run_id", "transition_count"],
        },
    },
    {
        "type": "function",
        "name": "ReportPipeline",
        "description": "解析指定检测报告并完成病害诊断、结构识别、Dr/BCI评分和统计分析",
        "parameters": {
            "type": "object",
            "properties": {
                "report_id": {"type": "string"},
                "facts": {"type": "integer"},
                "structured_only": {"type": "boolean"},
                "status": {"type": "string"},
                "scope": {"type": "string"},
            },
            "required": ["report_id", "status"],
        },
    },
    {
        "type": "function",
        "name": "ClaimVerifier",
        "description": "核验桥名、年份、幅别、病害、评分等输出主张与报告证据是否一致",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "passed": {"type": "boolean"},
                "deleted": {"type": "integer"},
                "flagged": {"type": "integer"},
                "scope": {"type": "string"},
            },
            "required": ["status"],
        },
    },
    {
        "type": "function",
        "name": "ScoreGapReconciler",
        "description": "核对报告发布Dr与系统重算Dr，计算差值并保持双轨评分证据",
        "parameters": {
            "type": "object",
            "properties": {
                "reported_dr": {"type": "number"},
                "computed_dr": {"type": "number"},
                "comparison_basis": {"type": "string"},
            },
            "required": ["reported_dr", "computed_dr", "comparison_basis"],
        },
    },
    {
        "type": "function",
        "name": "DirectDiagnosisEngine",
        "description": "按构件、病害和实测规模执行确定性构件级诊断",
        "parameters": {
            "type": "object",
            "properties": {
                "component": {"type": "string"},
                "disease": {"type": "string"},
                "width_mm": {"type": "number"},
                "area_m2": {"type": "number"},
            },
            "required": ["component", "disease"],
        },
    },
    {
        "type": "function",
        "name": "EnvironmentRiskAnalyzer",
        "description": "查询桥位环境画像并计算环境因子对病害风险和处治时间窗的修正",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "observation": {"type": "string"},
                "base_severity": {"type": "string"},
            },
            "required": ["city", "observation"],
        },
    },
    {
        "type": "function",
        "name": "KnowledgeGraphQuery",
        "description": "查询构件、病害、成因、处治措施与规范条款之间的知识图谱路径",
        "parameters": {
            "type": "object",
            "properties": {
                "component": {"type": "string"},
                "disease": {"type": "string"},
                "min_clause_weight": {"type": "integer"},
            },
            "required": ["component", "disease"],
        },
    },
    {
        "type": "function",
        "name": "QueryNormalizer",
        "description": "对残缺或口语化桥梁指令生成待用户确认的规范化候选，不提升为工程事实",
        "parameters": {
            "type": "object",
            "properties": {
                "candidate_rewrite": {"type": "string"},
                "candidates": {"type": "array", "items": {"type": "object"}},
                "fact_promotion_allowed": {"type": "boolean"},
                "status": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": [
                "candidate_rewrite",
                "candidates",
                "fact_promotion_allowed",
                "status",
            ],
        },
    },
    {
        "type": "function",
        "name": "InstructionConflictGuard",
        "description": "识别必须执行与必须不执行等互相冲突的要求并请求澄清",
        "parameters": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
        },
    },
    {
        "type": "function",
        "name": "CapabilityBoundaryGuard",
        "description": "拒绝调用系统中不存在的工具并返回真实能力边界",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "requested_tool": {"type": "string"},
            },
            "required": ["status", "requested_tool"],
        },
    },
]

# Backward-compatible import name. It is NOT a default registry or a capability
# claim. New integrations should pass their actual definitions to agent_trace.
TOOL_DEFINITIONS = REFERENCE_ONLY_TOOL_DEFINITIONS

_ACTIVE_TRACE: contextvars.ContextVar["TraceSession | None"] = contextvars.ContextVar(
    "bip_active_native_trace", default=None
)
_WRITE_LOCK = threading.Lock()


def _now_ns() -> int:
    return time.time_ns()


def _hex_id(byte_count: int) -> str:
    value = secrets.token_hex(byte_count)
    if set(value) == {"0"}:  # OpenTelemetry IDs may not be all zeroes.
        return ("1" + value[1:])
    return value


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _any_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    # Complex GenAI fields must be JSON encoded in an OTLP stringValue.
    return {"stringValue": _json_text(value)}


def _attributes(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": key, "value": _any_value(value)}
        for key, value in values.items()
        if value is not None
    ]


def _trace_directory() -> Path:
    configured = os.environ.get("ENGINEERING_AGENT_TRACE_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_TRACE_DIR


def trace_path(target: str) -> Path:
    name = "traces.json" if target == "static" else "traces-dynamic.json"
    return _trace_directory() / name


def _empty_document() -> dict[str, Any]:
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _attributes(
                        {
                            "service.name": SERVICE_NAME,
                            "service.version": INSTRUMENTATION_VERSION,
                        }
                    )
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": INSTRUMENTATION_NAME,
                            "version": INSTRUMENTATION_VERSION,
                        },
                        "spans": [],
                    }
                ],
            }
        ]
    }


def reset_trace_file(target: str) -> Path:
    """Start a fresh static or dynamic capture file."""
    path = trace_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(_empty_document(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    return path


def _append_spans(target: str, spans: Iterable[dict[str, Any]]) -> Path:
    path = trace_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        if path.exists():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                document = _empty_document()
        else:
            document = _empty_document()
        destination = document["resourceSpans"][0]["scopeSpans"][0]["spans"]
        destination.extend(spans)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    return path


class SpanHandle:
    def __init__(
        self,
        session: "TraceSession | None",
        name: str,
        operation: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.name = name
        self.operation = operation
        self.values = dict(attributes or {})
        self.span_id = _hex_id(8) if session else ""
        self.parent_span_id = session.current_parent_span_id if session else ""
        self.started_ns = 0
        self.ended_ns = 0
        self.status_code = 1
        self.status_message = ""
        self._parent_token: contextvars.Token[str] | None = None

    def __enter__(self) -> "SpanHandle":
        if self.session:
            self.started_ns = _now_ns()
            self._parent_token = self.session._current_parent.set(self.span_id)
        return self

    def set_attribute(self, key: str, value: Any) -> None:
        if self.session:
            self.values[key] = value

    def set_output(self, text: str, finish_reason: str = "stop") -> None:
        self.set_attribute(
            "gen_ai.output.messages",
            [
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "content": str(text or "")}],
                    "finish_reason": finish_reason,
                }
            ],
        )

    def mark_error(self, error: BaseException | str) -> None:
        if not self.session:
            return
        self.status_code = 2
        self.status_message = str(error)[:1000]
        self.values["error.type"] = type(error).__name__ if isinstance(error, BaseException) else "RuntimeError"

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        if not self.session:
            return False
        if exc is not None:
            self.mark_error(exc)
        self.ended_ns = max(_now_ns(), self.started_ns)
        if self._parent_token is not None:
            self.session._current_parent.reset(self._parent_token)
        self.session._spans.append(self._as_otlp())
        return False

    def _as_otlp(self) -> dict[str, Any]:
        values = {"gen_ai.operation.name": self.operation, **self.values}
        span: dict[str, Any] = {
            "name": self.name,
            "traceId": self.session.trace_id if self.session else "",
            "spanId": self.span_id,
            "startTimeUnixNano": str(self.started_ns),
            "endTimeUnixNano": str(self.ended_ns),
            "status": {"code": self.status_code},
            "attributes": _attributes(values),
        }
        if self.parent_span_id:
            span["parentSpanId"] = self.parent_span_id
        if self.status_message:
            span["status"]["message"] = self.status_message
        return span


class TraceSession:
    """One real agent request and all child operations produced by it."""

    def __init__(
        self,
        input_messages: list[dict[str, Any]],
        *,
        conversation_id: str,
        target: str = "dynamic",
        tool_definitions: list[dict[str, Any]] | None = None,
    ) -> None:
        if tool_definitions is not None and (
            not isinstance(tool_definitions, list)
            or any(not isinstance(row, dict) for row in tool_definitions)
        ):
            raise TypeError("tool_definitions must be an explicit list of mappings")
        self.trace_id = _hex_id(16)
        self.root_span_id = _hex_id(8)
        self.target = "static" if target == "static" else "dynamic"
        self.input_messages = input_messages
        # Definitions are caller-declared capabilities, not proof of execution.
        # Execution evidence is produced by child spans around real tool calls.
        self.tool_definitions = deepcopy(tool_definitions or [])
        self.conversation_id = conversation_id or f"eval-{self.trace_id[:12]}"
        self.started_ns = 0
        self.ended_ns = 0
        self.status_code = 1
        self.status_message = ""
        self.output_text = ""
        self.finish_reason = "stop"
        self._spans: list[dict[str, Any]] = []
        self._current_parent: contextvars.ContextVar[str] = contextvars.ContextVar(
            f"bip_trace_parent_{self.trace_id}", default=self.root_span_id
        )
        self._active_token: contextvars.Token[TraceSession | None] | None = None
        self._closed = False

    @property
    def current_parent_span_id(self) -> str:
        return self._current_parent.get()

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.root_span_id}-01"

    def __enter__(self) -> "TraceSession":
        self.started_ns = _now_ns()
        self._active_token = _ACTIVE_TRACE.set(self)
        return self

    def child(
        self,
        name: str,
        operation: str,
        attributes: dict[str, Any] | None = None,
    ) -> SpanHandle:
        return SpanHandle(self, name, operation, attributes)

    def finish(self, output_text: str, finish_reason: str = "stop") -> None:
        self.output_text = str(output_text or "")
        self.finish_reason = finish_reason

    def record_tool_calls(self, calls: Any) -> None:
        """Import caller-supplied records, not measure original tool durations.

        These spans measure this recording operation. For execution timing and
        failure status, wrap the real call with ``with trace.child(...)``.
        """
        if not isinstance(calls, list):
            return
        for index, raw in enumerate(calls):
            call = raw if isinstance(raw, dict) else {"result": raw}
            name = str(call.get("tool") or call.get("name") or call.get("role") or "bridge_runtime")
            call_id = str(call.get("id") or f"{self.trace_id[:12]}-{index + 1}")
            arguments = call.get("arguments")
            if arguments is None:
                arguments = {
                    key: value
                    for key, value in call.items()
                    if key not in {"tool", "name", "role", "id", "result", "output"}
                }
            result = call.get("result", call.get("output", call))
            data_source_id = call.get("data_source_id")
            if not data_source_id and name == "BridgeArchiveSearch":
                data_source_id = call.get("report_id")
            with self.child(
                f"execute_tool {name}",
                "execute_tool",
                {
                    "gen_ai.tool.name": name,
                    "gen_ai.tool.type": "function",
                    "gen_ai.tool.call.id": call_id,
                    "gen_ai.tool.call.arguments": arguments,
                    "gen_ai.tool.call.result": result,
                    "gen_ai.data_source.id": data_source_id,
                },
            ):
                pass

    def mark_error(self, error: BaseException | str) -> None:
        self.status_code = 2
        self.status_message = str(error)[:1000]
        if not self.output_text:
            self.output_text = self.status_message
        self.finish_reason = "error"

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        if exc is not None:
            self.mark_error(exc)
        self.close()
        return False

    def close(self) -> Path:
        if self._closed:
            return trace_path(self.target)
        self._closed = True
        self.ended_ns = max(_now_ns(), self.started_ns)
        if self._active_token is not None:
            _ACTIVE_TRACE.reset(self._active_token)
        root_values = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "engineering-domain-agent",
            "gen_ai.conversation.id": self.conversation_id,
            "engineering.agent_policy": "证据不足、要求矛盾或工具不存在时明确说明边界；不编造检测事实。",
            "gen_ai.input.messages": self.input_messages,
            "gen_ai.output.messages": [
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "content": self.output_text}],
                    "finish_reason": self.finish_reason,
                }
            ],
            "gen_ai.tool.definitions": self.tool_definitions,
        }
        root_span: dict[str, Any] = {
            "name": "invoke_agent engineering-domain-agent",
            "traceId": self.trace_id,
            "spanId": self.root_span_id,
            "startTimeUnixNano": str(self.started_ns),
            "endTimeUnixNano": str(self.ended_ns),
            "status": {"code": self.status_code},
            "attributes": _attributes(root_values),
        }
        if self.status_message:
            root_span["status"]["message"] = self.status_message
        # Children were completed during the request; keep the root first for readability.
        return _append_spans(self.target, [root_span, *self._spans])


def current_trace() -> TraceSession | None:
    return _ACTIVE_TRACE.get()


def model_call_span(
    provider: str,
    model: str,
    system_prompt: str,
    user_content: str,
) -> SpanHandle:
    session = current_trace()
    if session is None:
        return SpanHandle(None, "", "chat")
    provider_text = str(provider or "").strip().lower()
    provider_value = next(
        (
            canonical
            for marker, canonical in (
                ("openai", "openai"),
                ("anthropic", "anthropic"),
                ("cohere", "cohere"),
                ("bedrock", "aws.bedrock"),
                ("vertex", "gcp.vertex_ai"),
                ("gemini", "gcp.gemini"),
                ("watson", "ibm.watsonx.ai"),
            )
            if marker in provider_text
        ),
        None,
    )
    return session.child(
        f"chat {model or provider or 'llm'}",
        "chat",
        {
            "gen_ai.request.model": model or provider or "unknown",
            # Omit providers outside the registry enum instead of mislabelling them.
            "gen_ai.provider.name": provider_value,
            "gen_ai.system_instructions": [
                {"type": "text", "content": str(system_prompt or "")}
            ],
            "gen_ai.input.messages": [
                {
                    "role": "user",
                    "parts": [{"type": "text", "content": str(user_content or "")}],
                }
            ],
        },
    )


def agent_trace(
    input_messages: list[dict[str, Any]],
    *,
    conversation_id: str,
    target: str = "dynamic",
    tool_definitions: list[dict[str, Any]] | None = None,
) -> TraceSession:
    return TraceSession(
        input_messages, conversation_id=conversation_id, target=target,
        tool_definitions=tool_definitions,
    )
