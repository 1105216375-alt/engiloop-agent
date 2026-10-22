# -*- coding: utf-8 -*-
"""
R60 v2格式报告解析器(独立模块)

适用格式:
    序号 | 位置 | 种类 | 具体描述 | 照片编号
    1   | 桥面铺装 | 网状裂缝 | 行车道左幅第1联,网状裂缝,s=33m×6m | 2.1-1
    2   | 主梁    | 斜向裂缝 | 第2跨3号梁左侧腹板,距离1号墩1m,斜向裂缝1处,l=1.3m w=0.3mm | 2.2-1

输出与 pipeline._parse_record 相同的字段结构,可直接被下游使用。

设计原则:
- 完全独立,不依赖 pipeline.py 的内部函数
- 表格列名识别失败时,自动跳过(让旧 pipeline 兜底)
- 构件名识别基于"位置"列(短文本,准),编号从"具体描述"列提取
- 病害名识别基于"种类"列(短文本,准),失败再扫描描述列
"""

import re
from typing import List, Dict

from . import ontology


# ─────────────────────────────────────────
# 表头识别
# ─────────────────────────────────────────
_HEADERS_POS = ["位置", "构件", "部位", "构件名称", "构件位置"]
_HEADERS_KIND = ["种类", "病害", "缺损", "病害名称", "病害类型", "缺陷类型"]
_HEADERS_DESC = ["具体描述", "病害描述", "缺损描述", "现状描述", "描述"]


def _is_v2_table(table) -> bool:
    """判断表格是否是v2格式(必须同时含 位置/种类/描述 三个表头)"""
    if len(table.rows) < 2:
        return False
    head_cells = [c.text.strip() for c in table.rows[0].cells]
    has_pos = any(any(h == c or h in c for h in _HEADERS_POS) for c in head_cells)
    has_kind = any(any(h == c or h in c for h in _HEADERS_KIND) for c in head_cells)
    has_desc = any(any(h == c or h in c for h in _HEADERS_DESC) for c in head_cells)
    return has_pos and has_kind and has_desc


def _find_v2_columns(head_cells: List[str]):
    """从表头找出 位置/种类/描述/长度/宽度 列号"""
    col_pos = col_kind = col_desc = col_len = col_wid = -1
    _HEADERS_LEN = ["长度", "长度（m）", "长度(m)", "裂缝长度"]
    _HEADERS_WID = ["宽度", "宽度（mm）", "宽度(mm)", "裂缝宽度"]
    for i, c in enumerate(head_cells):
        c = c.strip()
        if col_pos == -1 and any(h == c or h in c for h in _HEADERS_POS):
            col_pos = i
        elif col_kind == -1 and any(h == c or h in c for h in _HEADERS_KIND):
            col_kind = i
        elif col_desc == -1 and any(h == c or h in c for h in _HEADERS_DESC):
            col_desc = i
        # R60: 长度/宽度独立列(示例桥7列格式)
        if col_len == -1 and any(h == c or h in c for h in _HEADERS_LEN):
            col_len = i
        if col_wid == -1 and any(h == c or h in c for h in _HEADERS_WID):
            col_wid = i
    return col_pos, col_kind, col_desc, col_len, col_wid


# ─────────────────────────────────────────
# 构件识别(从"位置"列短文本)
# ─────────────────────────────────────────
_COMPONENT_RULES = [
    # 优先级高的具体构件在前
    (r"雨篦|雨水箅子|篦子|泄水管|排水管|泄水", "排水设施"),
    (r"牛腿|齿块", "牛腿"),
    (r"(桥头)?搭板|过渡板", "桥头搭板"),
    (r"铰缝|板间铰缝", "铰缝"),
    (r"横隔[板梁]", "横隔板"),
    (r"翼[缘]?板", "翼板"),
    (r"湿接缝", "湿接缝"),
    (r"挂梁|悬挂梁", "挂梁"),
    # 主梁(覆盖 T梁/箱梁/空心板/预制梁)
    (r"T梁|箱梁|空心板|主梁|预制梁|预制板", "主梁"),
    (r"盖梁|墩帽", "盖梁"),
    (r"桥台|台身|台帽|台背", "桥台"),
    (r"桥墩|墩柱|墩身|薄壁墩", "桥墩"),
    (r"支座垫石|垫石", "支座垫石"),
    (r"抗震挡块|限位挡块|挡块", "挡块"),
    (r"支座", "支座"),
    (r"桥面铺装|沥青铺装|水泥铺装|铺装", "桥面铺装"),
    (r"伸缩缝|伸缩装置", "伸缩缝"),
    (r"防水层", "防水层"),
    (r"护栏|防撞|栏杆", "护栏"),
    (r"人行道", "人行道"),
    (r"声屏障", "声屏障"),
    (r"斜拉索|拉索", "斜拉索"),
    (r"主缆", "主缆"),
    (r"吊杆", "吊杆"),
    (r"拱圈|拱肋|主拱圈", "拱圈"),
    (r"承台", "承台"),
    (r"桩基|桩", "桩基"),
    (r"基础", "基础"),
    (r"翼墙|耳墙", "翼墙"),
    (r"锥坡|护坡", "锥坡"),
]


def _classify_component(text: str) -> str:
    """从"位置"列短文本识别构件类。"""
    text = (text or "").strip()
    if not text:
        return "其他"
    for pat, cls in _COMPONENT_RULES:
        if re.search(pat, text):
            return cls
    return "其他"


def _classify_disease(text: str, component_hint: str = "") -> str:
    """从"种类"列短文本识别病害名。"""
    return ontology.classify_disease_text(text, component_hint) or "未知病害"


# ─────────────────────────────────────────
# 构件实例编号提取(从"具体描述"列)
# 例如: "第2跨3号梁" → "3号梁",  "距离5号墩8m牛腿" → "5号墩牛腿"
# ─────────────────────────────────────────
def _extract_instance(desc: str, comp_cls: str) -> str:
    """从描述里提取构件实例编号。失败返回空串。"""
    desc = desc or ""
    if comp_cls == "主梁":
        # 跨号-梁号 / 单号梁
        m = re.search(r"(\d+[-]\d+(?:[-]\d+)?号?T?[梁板])", desc)
        if m:
            return m.group(1)
        m = re.search(r"(\d+[#号]T?[梁板])(?![墩台])", desc)
        if m:
            return m.group(1)
    elif comp_cls == "桥墩":
        m = re.search(r"((?:第)?\d+[#号]?墩)", desc)
        if m:
            return m.group(1)
    elif comp_cls == "桥台":
        m = re.search(r"((?:大|小)桩号(?:桥)?台|(?:左|右|南|北|东|西)(?:侧)?(?:桥)?台|\d+[#号]?桥?台|0[#号]?桥?台)", desc)
        if m:
            return m.group(1)
    elif comp_cls == "盖梁":
        m = re.search(r"((?:第)?\d+[号#]?盖梁)", desc)
        if m:
            return m.group(1)
    elif comp_cls == "支座":
        m = re.search(r"(\d+[-]\d+(?:[-]\d+)?(?:[~～]\d+)?[号#]?支座)", desc)
        if m:
            return m.group(1)
        m = re.search(r"(\d+[#号]支座)", desc)
        if m:
            return m.group(1)
    elif comp_cls == "牛腿":
        # "5号墩牛腿" / "4号梁牛腿" / "牛腿"
        m = re.search(r"(\d+[#号]?[墩台梁]\s*牛腿)", desc)
        if m:
            return m.group(1)
        if "牛腿" in desc:
            return "牛腿"
    elif comp_cls == "横隔板":
        m = re.search(r"(\d+号梁与\d+号梁横隔板|\d+[-]\d+(?:[-]\d+)?[号#]?横隔[板梁])", desc)
        if m:
            return m.group(1)
        if "横隔" in desc:
            return "横隔板"
    elif comp_cls == "伸缩缝":
        m = re.search(r"(\d+[#号]?伸缩缝)", desc)
        if m:
            return m.group(1)
    return ""


# ─────────────────────────────────────────
# 规模数据提取(宽度/长度/面积/处数)
# ─────────────────────────────────────────
def _parse_extent(desc: str) -> Dict:
    """从描述提取规模数据。"""
    data = {}
    desc = desc or ""

    # 宽度 mm: w=0.3mm / W=0.5 mm
    m = re.search(r"[wW][=＝]?\s*(\d+\.?\d*)\s*mm", desc)
    if m:
        data["width"] = float(m.group(1))

    # 长度 m: l=1.3m / L=2 m
    m = re.search(r"[lL][=＝]?\s*(\d+\.?\d*)\s*m(?!m)", desc)
    if m:
        data["length"] = float(m.group(1))

    # 面积 m²: s=33m×6m / S=5m×2m / S=1.3m×0.4m / s=0.2×0.3m²
    m = re.search(r"[sS]\d*[=＝]\s*(\d+\.?\d*)\s*m?\s*[×xX*]\s*(\d+\.?\d*)", desc)
    if m:
        try:
            a = float(m.group(1))
            b = float(m.group(2))
            data["area"] = round(a * b, 2)
        except Exception:
            pass

    # 处数: 1处 / 2处
    m = re.search(r"(\d+)\s*处", desc)
    if m:
        data["count"] = int(m.group(1))

    return data


# ─────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────
def extract_defects_v2(docx_path: str) -> List[Dict]:
    """从v2格式 docx 报告提取病害记录。

    输入: docx 文件路径
    输出: 病害记录列表(与 _parse_record 输出兼容)
    """
    from docx import Document

    doc = Document(docx_path)
    defects = []

    for table in doc.tables:
        if not _is_v2_table(table):
            continue

        head_cells = [c.text.strip() for c in table.rows[0].cells]
        col_pos, col_kind, col_desc, col_len, col_wid = _find_v2_columns(head_cells)

        if col_pos == -1 or col_kind == -1 or col_desc == -1:
            continue

        max_col = max(col_pos, col_kind, col_desc)
        if col_len != -1: max_col = max(max_col, col_len)
        if col_wid != -1: max_col = max(max_col, col_wid)

        for r in range(1, len(table.rows)):
            row = table.rows[r]
            if len(row.cells) <= max_col:
                continue

            comp_text = row.cells[col_pos].text.strip().replace("\n", " ")
            kind_text = row.cells[col_kind].text.strip().replace("\n", " ")
            desc_text = row.cells[col_desc].text.strip().replace("\n", " ")

            # 跳过空行
            if not desc_text or len(desc_text) < 5:
                continue
            # 跳过表头/小标题行(如"位置"、"种类"误认为数据)
            if comp_text in _HEADERS_POS or kind_text in _HEADERS_KIND:
                continue
            # 正常结论和支座活动方向属于检查信息，不是病害记录。
            if (ontology.is_non_disease_description(desc_text)
                    or ontology.is_non_disease_description(kind_text)):
                continue
            # R60: 跳过已修复/已修补的病害(不计入统计、分析、评分)
            repaired_phrases = [
                "已修复", "已修补", "已处理", "已整治", "已加固",
                "已更换", "已维修", "修复完成", "修补完成", "处治完成",
            ]
            if any(p in desc_text for p in repaired_phrases):
                continue
            # 种类列也可能写"已修复"
            if any(p in kind_text for p in repaired_phrases):
                continue

            # 1. 构件识别(基于位置列,失败再扫描描述列前 50 字)
            comp_cls = _classify_component(comp_text)
            if comp_cls == "其他":
                comp_cls_fb = _classify_component(desc_text[:50])
                if comp_cls_fb != "其他":
                    comp_cls = comp_cls_fb

            # R60: 位置列写"主梁"但描述明确提到"牛腿/搭板/铰缝"等悬挑/连接构件,
            # 应升级为更具体的构件类(这些是结构性病害的关键)
            if comp_cls == "主梁":
                if re.search(r"牛腿|齿块", desc_text):
                    comp_cls = "牛腿"
                elif re.search(r"(?<!桥)横隔[板梁]", desc_text):
                    comp_cls = "横隔板"
            # 位置列写"主梁"但描述含"桥头搭板"
            if comp_cls in ("主梁", "其他") and re.search(r"(桥头)?搭板|过渡板", desc_text):
                comp_cls = "桥头搭板"

            # 2. 病害识别(基于种类列,失败再扫描描述列)
            disease_name = _classify_disease(kind_text, comp_cls)
            if disease_name == "未知病害":
                disease_name = _classify_disease(desc_text, comp_cls)

            # 3. 实例名(带编号),从描述列提取
            instance = _extract_instance(desc_text, comp_cls)
            comp_name = instance if instance else (comp_text if comp_text else comp_cls)

            # 4. 规模数据(先从描述文本提取)
            extent_data = _parse_extent(desc_text)

            # R60: 从独立的长度/宽度列读取(7列格式如示例桥)
            # 独立列的值优先级高于描述文本里提取的(更准确)
            if col_len != -1 and len(row.cells) > col_len:
                len_text = row.cells[col_len].text.strip()
                if len_text:
                    try:
                        lv = float(len_text)
                        if lv > 0:
                            extent_data["length"] = lv  # 单位: m(表头写的)
                    except ValueError:
                        pass
            if col_wid != -1 and len(row.cells) > col_wid:
                wid_text = row.cells[col_wid].text.strip()
                if wid_text:
                    try:
                        wv = float(wid_text)
                        if wv > 0:
                            extent_data["width"] = wv  # 单位: mm(表头写的)
                    except ValueError:
                        pass

            # 5. 预估标度(v2格式不带标度列,根据规模数据粗估,供 auto_grading 匹配失败时 fallback)
            #    这里只做保守估计,auto_grading 匹配成功时会被覆盖
            est_scale = 1
            w = extent_data.get("width", 0)  # mm
            a = extent_data.get("area", 0)   # m²
            l = extent_data.get("length", 0) # m
            cnt = extent_data.get("count", 1)

            if disease_name in ("斜向裂缝", "横向裂缝", "纵向裂缝", "竖向裂缝", "裂缝"):
                if w >= 0.5:
                    est_scale = 4
                elif w >= 0.3:
                    est_scale = 3
                elif w >= 0.15:
                    est_scale = 2
                elif l >= 3:
                    est_scale = 2
            elif disease_name in ("网状裂缝",):
                # 桥面铺装网裂的"面积"是分布范围(如33m×6m=198m²),不是坑的面积
                # 阈值要比坑槽/破损大得多
                if a >= 500:
                    est_scale = 4
                elif a >= 100:
                    est_scale = 3
                elif a >= 10:
                    est_scale = 2
            elif disease_name in ("露筋", "锈胀露筋", "剥落露筋"):
                if a >= 2:
                    est_scale = 4
                elif a >= 0.5:
                    est_scale = 3
                else:
                    est_scale = 2  # 露筋至少 2 类
            elif disease_name in ("混凝土破损",):
                if a >= 5:
                    est_scale = 3
                elif a >= 0.5:
                    est_scale = 2
            elif disease_name in ("伸缩缝堵塞",):
                est_scale = 2
            # ── R60 补全:以下病害类型之前漏了 ──
            elif disease_name in ("坑槽",):
                if a >= 1:
                    est_scale = 3
                else:
                    est_scale = 2  # 坑槽至少 2 类
            elif disease_name in ("松散",):
                if a >= 10:
                    est_scale = 3
                else:
                    est_scale = 2
            elif disease_name in ("渗水泛碱",):
                if a >= 5:
                    est_scale = 3
                elif a >= 1:
                    est_scale = 2
                else:
                    est_scale = 2  # 渗水至少 2 类
            elif disease_name in ("蜂窝麻面",):
                if a >= 3:
                    est_scale = 3
                elif a >= 0.5:
                    est_scale = 2
                else:
                    est_scale = 2  # 蜂窝至少 2 类
            elif disease_name in ("锈蚀",):
                if a >= 5:
                    est_scale = 3
                else:
                    est_scale = 2  # 锈蚀至少 2 类
            elif disease_name in ("构件缺失",):
                est_scale = 3  # 缺失至少 3 类
            elif disease_name in ("支座脱空",):
                est_scale = 3
            elif disease_name in ("支座偏位",):
                est_scale = 2
            elif disease_name in ("支座老化", "剪切变形"):
                est_scale = 2
            elif disease_name in ("下挠",):
                est_scale = 3
            elif disease_name in ("断裂",):
                est_scale = 4
            elif disease_name in ("倾斜",):
                est_scale = 3
            elif disease_name in ("冲刷",):
                est_scale = 3

            # 6. 组装(与 _parse_record 输出字段兼容)
            defect = {
                "name_raw": comp_name,
                "component_cls": comp_cls,
                "component_name": comp_name,
                "disease_name": disease_name,
                "disease_detail": desc_text,
                "desc": desc_text,
                "scale": est_scale,
                "_scale_explicit": False,
                "_scale_source": "estimated",
                "type": comp_cls,
                "coord": {},
                "extent": "",
                "extent_data": extent_data,
            }
            defects.append(defect)

    return defects
