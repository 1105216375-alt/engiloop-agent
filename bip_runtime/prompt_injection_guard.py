from __future__ import annotations

import re
from typing import Any


_SENSITIVE_TARGET = re.compile(
    r"api\s*[-_ ]?key|apikey|密钥|系统提示词|系统指令|开发者提示|"
    r"隐藏指令|内部提示|system\s*prompt|developer\s*message|\.env",
    re.IGNORECASE,
)
_EXFILTRATION_ACTION = re.compile(
    r"输出|告诉|显示|打印|泄露|完整(?:给|输出|展示)?|读取|导出|发给|给我|查看|复制|忽略",
    re.IGNORECASE,
)


def inspect_prompt_injection(text: str) -> dict[str, Any]:
    content = str(text or "")
    target = _SENSITIVE_TARGET.search(content)
    action = _EXFILTRATION_ACTION.search(content)
    blocked = bool(target and action)
    return {
        "passed": not blocked,
        "blocked": blocked,
        "target": target.group(0) if target else "",
        "action": action.group(0) if action else "",
        "reason": "sensitive_prompt_or_credential_exfiltration" if blocked else "",
    }


def build_prompt_injection_reply() -> str:
    return (
        "安全边界：不能提供或回显 API Key、系统提示词、开发者指令、环境文件等敏感信息，"
        "也不会执行忽略规则或伪造病害的要求。若要继续桥梁分析，请只描述可核验的构件、"
        "病害、标度和现场证据。"
    )
