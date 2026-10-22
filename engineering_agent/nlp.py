"""Production NLU extraction plus explicitly new, offline intake adapters.

Source: legacy_v7/app.py. The parser functions below retain production logic.
Only the app/environment lookup is replaced; this module never imports the app,
reads production configuration, or starts a network/model client.
"""
from __future__ import annotations
import re
from typing import Any, Callable
from legacy_v7.core import ontology as _ontology
from .query_normalizer import QueryNormalizer
from bip_runtime.semantic_frame import build_semantic_frame, filter_diagnostic_items, compile_residual_plan
from bip_runtime.semantic_frame_v2 import build_semantic_frame_v2

_COMP_KEYWORDS = _ontology.build_comp_keywords_list()
_DISEASE_KEYWORDS = _ontology.build_disease_keywords_list()


def _extract_city(text):
    """Public adapter: no private environment database is loaded or inferred."""
    return None

_CN_LEVEL_DIGITS = {
    "一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5",
    "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
}


# Extracted from legacy_v7/app.py:720
def _cn_measurement_token(value: str) -> str:
    """Convert short spoken Chinese measurements such as 三 / 十二 / 零点三."""
    raw = str(value or "").strip().replace("〇", "零")
    digits = {
        "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    }
    if "点" in raw:
        integer, decimal = raw.split("点", 1)
        integer_value = _cn_measurement_token(integer or "零")
        decimal_value = "".join(str(digits[ch]) for ch in decimal if ch in digits)
        return f"{integer_value}.{decimal_value}" if decimal_value else integer_value
    if "十" in raw:
        left, right = raw.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return str(tens * 10 + ones)
    if raw in digits:
        return str(digits[raw])
    return raw


# Extracted from legacy_v7/app.py:742
def _normalize_colloquial_defect_text(text: str) -> str:
    """把弱 LLM/真人口语输入改写成规则解析能吃的桥梁病害短语。"""
    if not text:
        return text
    s = QueryNormalizer._pre_normalize(str(text))

    # 常见现场口误/形近字。只修正能与桥梁构件或病害共同成句的短语。
    s = s.replace("大樑", "主梁").replace("列逢", "裂缝")
    s = s.replace("之坐", "支座").replace("支坐", "支座")
    s = s.replace("柱子", "桥墩")
    s = re.sub(r"梁(?:肚子|腹|身子|身)", "主梁", s)
    s = s.replace("斜口子", "斜向裂缝").replace("斜道口子", "斜向裂缝")
    s = s.replace("裂口", "裂缝")
    s = re.sub(
        r"(桥面(?:铺装)?).{0,6}?(?:像|好像)?(?:起了?个?包|鼓了?个?包|鼓包)",
        r"\1铺装拥包",
        s,
    )
    s = re.sub(
        r"(?:车一过|车辆通过).{0,10}(?:咣当|跳一下|颠一下).{0,12}"
        r"(?:桥头(?:还|也)?跳|桥头跳车)",
        "桥头搭板桥头跳车",
        s,
    )
    s = re.sub(r"桥头(?:还|也)?跳(?:得厉害)?", "桥头搭板桥头跳车", s)
    s = re.sub(
        r"(桥墩|桥台|主梁)(?:掉皮|脱皮)(?:露铁|露筋)",
        r"\1混凝土破损+\1露筋",
        s,
    )
    s = re.sub(r"(?:那个)?梁底(?:下)?(?:也)?(?:湿|潮)", "主梁渗水", s)

    def _level_repl(match):
        value = _CN_LEVEL_DIGITS.get(match.group(1), match.group(1))
        return f"标度{value}"

    # "三级吧/四类/标度四/算三级" → "标度3/标度4"
    s = re.sub(r"标度\s*([一二两三四五12345])", _level_repl, s)
    s = re.sub(r"(?:算|按|为|是)?\s*([一二两三四五12345])\s*[级类]", _level_repl, s)

    # "0.3宽" 这种口语顺序,按裂缝宽度 mm 处理。
    s = re.sub(r"(\d+(?:\.\d+)?)\s*宽", r"宽\1mm", s)
    s = re.sub(r"(裂缝|斜裂缝|斜向裂缝|纵裂缝|横裂缝)\s*(\d+(?:\.\d+)?)\s*(mm|毫米)", r"\1宽\2\3", s)
    # 工程记录常把量测值写在病害前，如“0.30mm斜向裂缝”。只在数值
    # 紧邻裂缝词时解释为裂缝宽度，避免把普通长度或保护层厚度误绑定。
    s = re.sub(
        r"(\d+(?:\.\d+)?)\s*(mm|毫米)(?=\s*(?:斜向|斜|纵向|纵|横向|横|竖向|竖|网状)?裂缝)",
        r"宽\1\2",
        s,
    )
    s = re.sub(
        r"(裂缝|斜向裂缝|纵向裂缝|横向裂缝)"
        r".{0,6}?(?:大概|约|差不多|估计)\s*(\d+(?:\.\d+)?)\s*(mm|毫米)",
        r"\1宽\2\3",
        s,
    )
    chinese_number = r"[零〇一二两三四五六七八九十百点]+"

    def _cn_width_repl(match):
        return f"{match.group(1)}宽{_cn_measurement_token(match.group(2))}毫米"

    s = re.sub(
        rf"(斜向裂缝|纵向裂缝|横向裂缝|斜裂缝|纵裂缝|横裂缝|裂缝)"
        rf".{{0,4}}?({chinese_number})\s*毫米宽",
        _cn_width_repl,
        s,
    )

    # 口语构件。
    s = re.sub(r"边梁", "主梁", s)
    s = re.sub(r"(?<![桥主])梁(?=(有|上|里|底|裂|缝|开|出|部))", "主梁", s)

    # 口语裂缝。
    s = s.replace("斜缝", "斜向裂缝")
    s = s.replace("纵缝", "纵向裂缝")
    s = s.replace("横缝", "横向裂缝")
    s = re.sub(r"(有条|有一道|有一条)?斜向?裂", "斜向裂缝", s)
    s = re.sub(r"(有条|有一道|有一条)?纵向?裂", "纵向裂缝", s)
    s = re.sub(r"(有条|有一道|有一条)?横向?裂", "横向裂缝", s)
    s = re.sub(r"(有条|有一道|有一条)?裂了", "裂缝", s)
    s = re.sub(r"(有条|有一道|有一条)?开了?缝", "裂缝", s)
    s = re.sub(r"有(一条|一道|条|道)?缝", "裂缝", s)
    s = re.sub(
        r"(桥墩|桥台|主梁)裂(?:了|开了)?(?=$|[，,。；;、\s]|是|标度|宽)",
        r"\1裂缝",
        s,
    )

    # 口语露筋。
    s = re.sub(r"钢筋\s*(露出来了?|外露|露出|裸露)", "露筋", s)
    s = re.sub(r"(露出|外露|裸露)\s*钢筋", "露筋", s)
    s = s.replace("露钢筋", "露筋")

    # 口语支座脱空。
    s = re.sub(
        r"橡胶(?!支座|带|条)(?=.{0,8}(?:歪|悬空|没挨着|没贴住|翘起来|空着))",
        "支座",
        s,
    )
    s = re.sub(
        r"(?:桥底下?(?:那个|那)?|桥下面?(?:垫着梁)?(?:那块|那个)?|"
        r"墩头上垫梁的)橡胶(?!带|条|支座)",
        "支座",
        s,
    )
    s = re.sub(
        r"(?:桥底下?(?:那个|那)?|桥下面?(?:垫着梁)?(?:那块|那个)?|"
        r"墩头上垫梁的)?(?:黑胶块|橡胶块|橡胶垫|胶块|胶皮)",
        "支座",
        s,
    )
    s = re.sub(
        r"(支座.{0,12}?)(?:一边|一侧|左边|右边)?"
        r"(?:没贴住|没挨着|没顶住|翘起来了?|翘了|悬着|空着)",
        r"\1脱空",
        s,
    )
    s = re.sub(r"(支座.{0,10}?)(有点儿?空|好像也有点儿?空|空了|悬空|垫空)", r"\1脱空", s)
    s = re.sub(r"支座\s*空", "支座脱空", s)
    s = re.sub(r"(支座.{0,8}?)(?:有点|明显)?(?:歪|歪了)", r"\1支座偏位", s)

    # 口语伸缩缝病害。
    s = re.sub(r"(伸缩缝.{0,6}?)(坏了|烂了|破了|损坏)", r"\1破损", s)

    return s


# Extracted from legacy_v7/app.py:869
def _scan_component_positions(text):
    """
    扫描文本里出现的所有构件名位置。
    返回 [(start, end, alias, canonical), ...] 按 start 排序。

    用 ontology 的 _COMP_KEYWORDS(长串优先)来匹配,确保"桥墩"不会匹中"桥墩盖梁"
    这种长串里。同时去重(同一个 canonical 在同一个位置只保留一次)。
    """
    hits = []
    used_ranges = []  # 已被长串覆盖的区间
    for alias, canonical in _COMP_KEYWORDS:
        if len(alias) < 2:
            continue
        start = 0
        while True:
            idx = text.find(alias, start)
            if idx < 0:
                break
            end = idx + len(alias)
            # 检查这个位置是否已经被一个更长的别名覆盖
            covered = any(s <= idx and end <= e for s, e in used_ranges)
            if not covered:
                hits.append((idx, end, alias, canonical))
                used_ranges.append((idx, end))
            start = end
    hits.sort(key=lambda h: h[0])
    return hits


# Extracted from legacy_v7/app.py:898
def _split_multi_component_clause(text):
    """
    多构件感知拆分:如果一段文本里出现 ≥2 个构件,按构件位置切分成多段。

    例:
      "桥墩露筋支座脱空" → 扫描到 [桥墩@0, 支座@4]
                         → 切分为 ["桥墩露筋", "支座脱空"]
      "桥墩露筋,支座脱空" → 切分为 ["桥墩露筋", "支座脱空"](逗号不影响)
      "主梁有裂缝" → 1 个构件,不拆 → ["主梁有裂缝"]
    """
    # 跨构件纠正必须先于按构件切句。例如“不是支座脱空，是主梁裂缝”
    # 若先切句会把被否定的支座也送入诊断。
    replacement = re.search(
        r"(?:不是|并非)([^，,；;。]{1,36})[，,]?(?:而)?是([^，,；;。]{1,48})",
        text,
    )
    if replacement:
        asserted = replacement.group(2)
        has_component = any(alias in asserted for alias, _ in _COMP_KEYWORDS)
        has_disease = any(keyword in asserted for keyword, _ in _DISEASE_KEYWORDS)
        if has_component and has_disease:
            text = asserted + text[replacement.end():]

    comp_hits = _scan_component_positions(text)

    # 去重:同一个 canonical 连续出现只算一次,避免"桥墩盖梁"被拆出 3 段
    unique_hits = []
    seen_canonicals = set()
    for h in comp_hits:
        if h[3] not in seen_canonicals:
            unique_hits.append(h)
            seen_canonicals.add(h[3])

    if len(unique_hits) <= 1:
        return [text]

    # 按构件起始位置切分
    segments = []
    for i, (start, end, alias, canonical) in enumerate(unique_hits):
        if i + 1 < len(unique_hits):
            next_start = unique_hits[i + 1][0]
            segments.append(text[start:next_start])
        else:
            segments.append(text[start:])
    return [s.strip(",,。.; ;") for s in segments if s.strip()]


# Extracted from legacy_v7/app.py:945
def parse_chat_query(text):
    """
    从自然语言中提取病害诊断请求。

    核心改进:
    1. 按分隔符拆分成多个子句,每个子句独立解析
    2. 每个子句提取宽度/长度/面积等数值
    3. 构件-病害按邻近关系配对,不做笛卡尔积
    4. 城市全局提取(可能只出现一次)
    5. **多构件感知拆分**: 如果一个子句里出现多个不同构件,按构件位置二次切分

    返回: [{"comp", "disease", "scale", "city", "width", "length", "area"}, ...]
    """
    text = _normalize_colloquial_defect_text(text)

    # ── 全局提取城市 ──
    city = _extract_city(text)

    # ── 一次拆分:用强分隔符(换行/分号/加号/带空格的构件前缀) ──
    # 注意:不能直接用逗号拆,因为"主梁裂缝,宽0.3mm,泉州"里的逗号是参数分隔
    clauses = re.split(r'[;;\n]+|\s+(?=[主桥支伸护盖横湿翼铺排])', text)
    # 用 + 号拆
    expanded = []
    for c in clauses:
        expanded.extend(re.split(r'[++]', c))
    clauses = [c.strip() for c in expanded if c.strip()]

    # ── 二次拆分:多构件感知 ──
    # 对每个子句再扫一遍构件位置,如果有 ≥2 个不同构件就按构件位置切分
    # 这能修复 "桥墩露筋,支座脱空" / "桥墩露筋支座脱空" 这种 case
    final_clauses = []
    for c in clauses:
        sub = _split_multi_component_clause(c)
        final_clauses.extend(sub)
    clauses = final_clauses

    items = []
    for clause_index, clause in enumerate(clauses):
        item = _parse_single_clause(clause)
        if item:
            # 城市:子句级覆盖全局
            clause_city = _extract_city(clause)
            item["city"] = clause_city or city
            # 保留来源子句，供 SemanticFrame 做否定、修复、疑似和范围判定。
            # 下游诊断仍只读取原有字段，兼容旧数据契约。
            item["_source_clause"] = clause
            item["_source_index"] = clause_index
            # ── NLU 合理性检查(13 · 输入级 sanity check) ──
            item["warnings"] = _sanity_check(item)
            items.append(item)

    # “A 病害、B 病害，标度分别 3、4”属于跨子句分配，单句解析无法
    # 正确绑定。只在数量完全对应时覆盖，避免把年份或普通枚举误当标度。
    distributed = re.search(
        r"(?:标度|等级|评定)?\s*分别(?:为|是)?\s*"
        r"([一二两三四五12345](?:\s*[、,，/和及]\s*[一二两三四五12345])+)" ,
        text,
    )
    if distributed:
        scale_values = [
            int(_CN_LEVEL_DIGITS[value])
            for value in re.findall(r"[一二两三四五12345]", distributed.group(1))
        ]
        if len(scale_values) == len(items):
            for item, scale in zip(items, scale_values):
                item["scale"] = scale
                item["_scale_explicit"] = True

    # “前者2，后者4”是更口语的同义写法。只在恰好两个诊断条目且
    # 两个指代值都存在时绑定，避免把普通数字或年份误当标度。
    if len(items) == 2:
        former = re.search(
            r"前者\s*(?:标度|等级|评定)?\s*(?:为|是)?\s*([一二两三四五12345])",
            text,
        )
        latter = re.search(
            r"后者\s*(?:标度|等级|评定)?\s*(?:为|是)?\s*([一二两三四五12345])",
            text,
        )
        if former and latter:
            items[0]["scale"] = int(_CN_LEVEL_DIGITS[former.group(1)])
            items[0]["_scale_explicit"] = True
            items[1]["scale"] = int(_CN_LEVEL_DIGITS[latter.group(1)])
            items[1]["_scale_explicit"] = True

    for item in items:
        item["warnings"] = _sanity_check(item)

    return items


# Extracted from legacy_v7/app.py:1103
def _sanity_check(item):
    """
    对 NLU 提取的参数做领域常识检查,违反常识的标 warning。
    返回 warning 字符串列表,空列表表示一切正常。

    这是一道"输入级符号验证器":挡住明显不合理的解析结果,防止它一路传到
    评级引擎和 LLM 综合环节后造成莫名其妙的错答案。
    """
    warns = []
    comp = item.get("comp", "")
    disease = item.get("disease", "")
    width = item.get("width", 0)   # mm
    length = item.get("length", 0) # m
    area = item.get("area", 0)     # m²

    # 1. 裂缝宽度应在 [0.05mm, 50mm] 之间。> 50mm 已经不是裂缝是裂口
    if "裂" in disease or "缝" in disease:
        if width > 50:
            warns.append(f"裂缝宽度 {width}mm 异常偏大(>50mm 已不属于裂缝范畴),请确认单位是否为 mm")
        elif 0 < width < 0.05:
            warns.append(f"裂缝宽度 {width}mm 异常偏小(<0.05mm 难以肉眼观测),请确认数值")
        if length > 100:
            warns.append(f"裂缝长度 {length}m 异常偏大(>100m),请确认单位")

    # 2. 面积应在 [0.001, 1000] m²
    if area > 1000:
        warns.append(f"病害面积 {area}m² 异常偏大,请确认单位")

    # 3. 标度应在 1-5
    scale = item.get("scale", 0)
    if scale and (scale < 1 or scale > 5):
        warns.append(f"标度 {scale} 超出 1-5 范围,请确认")

    # 4. 构件-病害组合常识检查:某些病害不可能发生在某些构件上
    incompatible = {
        ("支座", "裂缝"): "支座一般不会出现'裂缝',更可能是'剪切变形'或'老化开裂'",
        ("伸缩缝", "裂缝"): "伸缩缝的常见问题是堵塞/异响/橡胶带破损,'裂缝'描述不规范",
    }
    key = (comp, disease)
    if key in incompatible:
        warns.append(incompatible[key])

    return warns


# Extracted from legacy_v7/app.py:1164
def _parse_single_clause(clause):
    """
    解析单个子句，提取构件、病害、标度、宽度/长度/面积。
    例: "主梁发现斜向裂缝，宽0.3mm，标度3"
    →  {"comp":"主梁", "disease":"斜向裂缝", "scale":3, "width":0.3, ...}
    """
    # 构件
    comp = None
    for kw, val in _COMP_KEYWORDS:
        if kw in clause:
            comp = val
            break

    # “不是脱空，是偏位”属于同一构件上的事实替换。病害识别只读取
    # 转折后的最终断言；若转折后没有可识别病害，则保留原句，兼容
    # “不是没有裂缝，是修过又开了”这类复发描述。
    disease_clause = clause
    replacement = re.search(
        r"(?:不是|并非)([^，,；;。]{1,24})[，,]?(?:而)?是([^，,；;。]{1,36})",
        clause,
    )
    if replacement and any(kw in replacement.group(2) for kw, _ in _DISEASE_KEYWORDS):
        disease_clause = (
            clause[:replacement.start()]
            + replacement.group(2)
            + clause[replacement.end():]
        )

    # 病害
    disease = None
    for kw, val in _DISEASE_KEYWORDS:
        if kw in disease_clause:
            disease = val
            break

    if not comp and not disease:
        return None

    # 关键修复：必须同时有构件 + 病害才算诊断条目
    # 否则 "桥梁技术状况怎么评定" 会被错误解析成"主梁裂缝"
    # 兜底1：只有病害没构件 → 根据病害推断构件
    if disease and not comp:
        disease_to_comp = {
            "坑槽": "桥面铺装",
            "网状裂缝": "桥面铺装",
            "支座脱空": "支座",
            "支座偏位": "支座",
            "支座老化": "支座",
            "伸缩缝堵塞": "伸缩缝",
        }
        if disease in disease_to_comp:
            comp = disease_to_comp[disease]
        else:
            return None  # 无法推断的就不诊断

    if not comp or not disease:
        return None

    # 标度
    scale = 1
    m = re.search(r'标度\s*(\d)', clause)
    scale_explicit = bool(m)
    if m:
        scale = int(m.group(1))

    # 宽度(归一化为 mm)
    # 关键修复:必须区分 mm/cm/m 三种单位,否则会把"0.2cm"当成"0.2mm"用,缩小10倍
    # 正则要点:
    #   1. 单位枚举里"mm/cm/厘米/毫米"必须排在单字符 m 前面(避免被 m 截胡)
    #   2. 数字部分 \d+\.?\d* 才能匹小数
    width = 0.0
    width_unit = None
    UNIT_PATTERN = r'(mm|cm|毫米|厘米|ｍｍ|ｃｍ|m|米)'

    m = re.search(
        r'宽[度]?\s*[=:：]?\s*(\d+(?:\.\d+)?)\s*' + UNIT_PATTERN,
        clause
    )
    if not m:
        m = re.search(
            r'[wW]\s*[=:：]?\s*(\d+(?:\.\d+)?)\s*' + UNIT_PATTERN,
            clause
        )
    if m:
        width = float(m.group(1))
        width_unit = m.group(2)
    else:
        # 不带单位的"宽 0.3"——按 mm 处理
        m = re.search(r'宽[度]?\s*[=:：]?\s*(\d+(?:\.\d+)?)(?!\s*' + UNIT_PATTERN + ')', clause)
        if m:
            width = float(m.group(1))
            width_unit = "mm"

    # 单位换算 → mm
    if width_unit in ("cm", "厘米", "ｃｍ"):
        width = width * 10
    elif width_unit in ("m", "米"):
        width = width * 1000

    # 长度(归一化为 m)
    length = 0.0
    length_unit = None
    m = re.search(
        r'长[度]?\s*[=:：]?\s*(\d+(?:\.\d+)?)\s*' + UNIT_PATTERN,
        clause
    )
    if not m:
        m = re.search(
            r'[lL]\s*[=:：]?\s*(\d+(?:\.\d+)?)\s*' + UNIT_PATTERN,
            clause
        )
    if m:
        length = float(m.group(1))
        length_unit = m.group(2)
    else:
        m = re.search(r'长[度]?\s*[=:：]?\s*(\d+(?:\.\d+)?)(?!\s*' + UNIT_PATTERN + ')', clause)
        if m:
            length = float(m.group(1))
            length_unit = "m"

    if length_unit in ("cm", "厘米"):
        length = length / 100
    elif length_unit in ("mm", "毫米"):
        length = length / 1000

    # 面积 (m²)
    area = 0.0
    m = re.search(r'面积\s*[=:：]?\s*(\d+\.?\d*)\s*(?:m[²2]|平方米)?', clause)
    if not m:
        m = re.search(r'[sS]\s*[=:：]?\s*(\d+\.?\d*)\s*m[²2]?', clause)
    if m:
        area = float(m.group(1))

    return {
        "comp": comp,
        "disease": disease,
        "scale": scale,
        "_scale_explicit": scale_explicit,
        "width": width,
        "length": length,
        "area": area,
        "city": None,  # filled by caller
    }


# ---------------------------------------------------------------------------
# Public-release adapters added in this extraction (not historical app code).
# ---------------------------------------------------------------------------
def compile_intake(
    text: str,
    *,
    session_context: dict[str, Any] | None = None,
    llm_call: Callable[[str, str], Any] | None = None,
    force_neural: bool = False,
    current_date=None,
) -> dict[str, Any]:
    """Run the extracted production parser and production semantic compiler.

    ``llm_call`` is an explicit optional dependency. No provider is configured
    here. An accepted source span establishes textual provenance, not truth.
    """
    text = str(text or "")
    context = dict(session_context or {})
    raw_items = parse_chat_query(text)
    scope_categories = {"上部结构", "下部结构", "桥面系", "附属设施"}
    component_mentions = [
        {"start": start, "end": end, "alias": alias,
         "canonical": alias if alias in scope_categories else canonical}
        for start, end, alias, canonical in _scan_component_positions(text)
    ]
    rule_frame = build_semantic_frame(
        text, raw_items, session_context=context,
        component_mentions=component_mentions,
    )
    frame = build_semantic_frame_v2(
        text, rule_frame, session_context=context, llm_call=llm_call,
        force_neural=force_neural, current_date=current_date,
    )
    safe_items, blocked_items = filter_diagnostic_items(raw_items, frame)
    return {
        "raw_input": text,
        "normalized_input": _normalize_colloquial_defect_text(text),
        "parsed_items": raw_items,
        "semantically_eligible_items": safe_items,
        "blocked_items": blocked_items,
        "semantic_frame": frame,
        "residual_plan": compile_residual_plan(frame),
        "engineering_verified": False,
        "boundary": "语义可接纳不等于现场事实确证；原文锚点不等于事实证明。",
    }


_ORAL_NUMBER = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十点]+)"
_MEASUREMENT_UNIT = r"(?:毫米|厘米|公分|mm|cm|米|m)"
_UNIT_IN_METERS = {"毫米": .001, "mm": .001, "厘米": .01,
                   "公分": .01, "cm": .01, "米": 1., "m": 1.}


def _oral_number(value: str) -> float | None:
    converted = _cn_measurement_token(value)
    if not re.fullmatch(r"\d+(?:\.\d+)?", converted):
        return None
    return float(converted)


class MeasurementIntakeSession:
    """New offline, single-defect intake demonstration with explicit gaps.

    This adapter is not the production execution-residual controller. It uses
    the real NLU/semantic core, then tracks missing observation fields. Only
    explicit numbers with explicit units and a claimed measuring action can
    fill measurement slots. User-reported measurements remain unverified.
    """

    def __init__(self) -> None:
        self.turns: list[str] = []
        self.fields: dict[str, Any] = {}
        self.provenance: dict[str, dict[str, Any]] = {}
        self.estimates: list[dict[str, Any]] = []
        self.conflicts: list[dict[str, Any]] = []

    def _put(self, name: str, value: Any, source_span: str,
             *, evidence_status: str = "user_reported") -> None:
        if name in self.fields and self.fields[name] != value:
            self.conflicts.append({"field": name, "old": self.fields[name],
                                   "new": value, "source_span": source_span})
            return
        self.fields[name] = value
        self.provenance[name] = {
            "turn": len(self.turns), "source_span": source_span,
            "evidence_status": evidence_status, "independently_verified": False,
        }

    def ingest(self, text: str) -> dict[str, Any]:
        """Consume one turn without inventing measurements or diagnosing risk."""
        text = str(text or "").strip()
        self.turns.append(text)
        intake = compile_intake(text)
        items = intake["semantically_eligible_items"]
        frame = intake["semantic_frame"]
        if len(items) == 1:
            self._put("component", items[0]["comp"], text)
            self._put("disease", items[0]["disease"], text)
        elif len(items) > 1:
            self.conflicts.append({"field": "observation_scope",
                                   "reason": "本示例仅接收单条病害，不能自动合并多条。"})
        if intake["blocked_items"] or frame.get("requires_clarification"):
            self.conflicts.append({"field": "existence_or_scope",
                                   "reason": "本轮含被否定、疑似或语义冲突的病害。"})

        instance = re.search(r"\d+(?:-\d+)*[#号]?(?:T梁|箱梁|主梁|号梁)", text)
        if instance:
            self._put("component_ref", instance.group(0), instance.group(0))
        surface = re.search(r"梁底|腹板|侧面|底板|顶板", text)
        if surface:
            self._put("surface", surface.group(0), surface.group(0))
        location_pattern = (
            rf"距(?P<reference>\d+号[台墩]|左侧|右侧)\s*"
            rf"(?P<number>{_ORAL_NUMBER})\s*(?P<unit>{_MEASUREMENT_UNIT})"
        )
        for match in re.finditer(location_pattern, text):
            value = _oral_number(match.group("number"))
            if value is None:
                continue
            reference = match.group("reference")
            key = "side_offset" if reference in {"左侧", "右侧"} else "reference_offset"
            self._put(key, {"reference": reference, "distance_m": round(
                value * _UNIT_IN_METERS[match.group("unit")], 9)}, match.group(0))

        # Deliberately stricter than the legacy parser's implicit-unit fallback.
        # A numeric-looking conversational phrase is never enough to certify it.
        measured = bool(re.search(r"尺|测缝卡|量了|量过|量到|量得|测得|实测", text))
        if re.search(r"(?:没|未|没有|尚未|还没|不曾).{0,8}(?:量|测|尺)|"
                     r"(?:假设|假装|虚构).{0,10}(?:量|测|尺)", text):
            measured = False
        approximate = bool(re.search(r"大概|大约|估计|差不多|约为|约[零一二三四五六七八九\d]", text))
        patterns = {
            "length_m": rf"(?:长度|长|[lL])\s*[=:：]?\s*(?P<number>{_ORAL_NUMBER})\s*(?P<unit>{_MEASUREMENT_UNIT})",
            "width": rf"(?:宽度|宽|[wW]|测缝卡(?:读数|是|为)?)\s*[=:：]?\s*(?P<number>{_ORAL_NUMBER})\s*(?P<unit>{_MEASUREMENT_UNIT})",
        }
        crack = "裂缝" in str(self.fields.get("disease", ""))
        for slot, pattern in patterns.items():
            for match in re.finditer(pattern, text):
                value = _oral_number(match.group("number"))
                if value is None or value <= 0:
                    continue
                key = slot if slot != "width" else ("width_mm" if crack else "width_m")
                value_m = value * _UNIT_IN_METERS[match.group("unit")]
                normalized = round(value_m * (1000 if key == "width_mm" else 1), 9)
                if not measured or approximate:
                    self.estimates.append({"field": key, "source_span": match.group(0),
                                           "status": "not_accepted_as_measurement"})
                    continue
                self._put(key, normalized, match.group(0),
                          evidence_status="user_reported_measurement")

        width_key = "width_mm" if crack else "width_m"
        required = ["component", "disease", "component_ref", "surface",
                    "reference_offset", "side_offset", "length_m", width_key]
        missing = [name for name in required if name not in self.fields]
        questions = []
        if "component" in missing or "disease" in missing:
            questions.append("请先说明是哪类构件、现场看到什么病害；不能确认的请保留为疑似。")
        location_labels = {"component_ref": "构件编号", "surface": "所在表面",
                           "reference_offset": "距哪座桥台或桥墩多远",
                           "side_offset": "距哪一侧多远"}
        location_gaps = [label for name, label in location_labels.items() if name in missing]
        if location_gaps:
            questions.append("请补充" + "、".join(location_gaps) + "。")
        if "length_m" in missing or width_key in missing:
            questions.append("请补充尺量长度和宽度，带上单位；裂缝宽度可报测缝卡读数，不能把比喻当量值。")
        if not missing:
            questions.append("量测口述已规范化。还需核对构件定位、原始照片和测量记录，当前不直接定级。")
        if self.conflicts:
            questions.insert(0, "存在前后冲突或不确定描述，请人工核对；本示例不会自动覆盖先前信息。")
        return {
            "adapter": "new_public_single_defect_intake_v1",
            "turn": len(self.turns), "symbol": dict(self.fields),
            "provenance": dict(self.provenance), "missing_fields": missing,
            "residual": {"observation_gaps": missing,
                         "verification_gaps": ["构件定位复核", "原始照片与量测记录核验"]},
            "unaccepted_estimates": list(self.estimates),
            "conflicts": list(self.conflicts), "next_question": "\n".join(questions),
            "record_status": "待复核", "engineering_verified": False,
            "eligible_for_scoring": False,
            "semantic_parser_mode": frame["parser_mode"],
        }
