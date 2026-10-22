"""Purely synthetic, offline conversation using the extracted production NLU.

Run from the repository root: python -m examples.semantic_intake
No production archive, API configuration, model client, or state DB is used.
"""
import json

from engineering_agent.nlp import MeasurementIntakeSession, compile_intake


def main():
    print("【合成案例】养护口语 → 缺口追问 → 单位归一 → 待复核符号记录")
    session = MeasurementIntakeSession()
    for text in (
        "1-1号T梁梁底有一处锈胀露筋。",
        "在距0号台6米、距右侧0米的位置，一巴掌长、两指宽。",
        "尺子量过：长1.5米，宽十厘米。",
    ):
        result = session.ingest(text)
        print(f"\n养护工：{text}")
        print(f"系统：{result['next_question']}")
        print("当前信息缺口：", result["missing_fields"])
    print("\n简洁符号结构（非现场事实确证，不直接定级）：")
    print(json.dumps({
        "symbol": result["symbol"], "record_status": result["record_status"],
        "verification_gaps": result["residual"]["verification_gaps"],
        "eligible_for_scoring": result["eligible_for_scoring"],
    }, ensure_ascii=False, indent=2))

    print("\n【合成反例】未报告 ≠ 没有，也 ≠ 已存在")
    uncertain = compile_intake("主梁没说有裂缝，只是建议复检，不要生成报告。")
    print(json.dumps({
        "blocked": uncertain["semantic_frame"]["defect_mentions"],
        "constraints": uncertain["semantic_frame"]["constraints"],
        "engineering_verified": uncertain["engineering_verified"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
