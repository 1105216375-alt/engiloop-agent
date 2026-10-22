"""
桥梁病害诊断系统 · 统一术语本体(Ontology)

这是项目里所有"构件名/病害名同义词映射"的单一可信源。
之前散落在 5 个文件里的别名表(_COMP_KEYWORDS / COMPONENT_CATEGORY_MAP /
_COMPONENT_KEYWORDS / DECK_COMPONENTS / ...)统一从这里导出。

设计原则:
  1. canonical name 必须与 expert_kb / knowledge_base 里的实际 components 字段值一致
     (保证 db 查询能 fast path 命中,不需要降级)
  2. aliases 包括所有用户口语、清洗前的旧名、英文别名
  3. regulation_keywords 是规范条文文本里出现的更宽泛的词族
     (例如 "上部承重构件" 不会作为构件别名,但会出现在规范文本里)
  4. 提供语义查询方法:are_equivalent / is_load_bearing / is_deck / ...

新增构件/病害的工作流:
  - 添加到 _COMPONENT_DEFS / _DISEASE_DEFS
  - 不需要改任何其他文件
  - 自动通过下面的 build_*_index 函数被各模块使用
"""
import re
from typing import Dict, List, Optional, Set, Tuple


# ══════════════════════════════════════════════════════════
# 构件本体
# ══════════════════════════════════════════════════════════

# 关键约定:canonical 必须与 expert_kb.json 里的 components 字段值精确一致!
_COMPONENT_DEFS: Dict[str, Dict] = {

    # ─── 上部结构 ───
    "主梁": {
        "category": "上部结构",
        "aliases": [
            "梁体", "梁部", "梁底",
            # 梁的部位(腹板/底板/顶板等;在 expert_kb 里只出现在 defect 字段,
            # 不会跟 components 字段冲突,这里加 alias 是为了让 NLU 能识别
            # "主梁腹板斜裂缝/箱梁底板纵向裂缝" 这种工程报告标准描述)
            "腹板", "梁腹板", "底板", "梁底板", "顶板", "梁顶板",
            # 梁的常见近义词(expert_kb 里出现过的旧名)
            "系梁", "系杆", "混凝土梁",
            # R60 新增:大类口语 fallback(用户说"上部结构裂缝"时归到主梁)
            "上部结构", "上部承重构件", "上部",
        ],
        "regulation_keywords": [
            "主梁", "梁体", "梁部", "上部承重构件", "上部结构",
            "钢筋混凝土梁", "预应力混凝土梁", "梁式桥", "梁桥",
        ],
        "is_load_bearing": True,
    },
    "T梁": {
        "category": "上部结构",
        "aliases": ["T形梁", "T型梁"],
        "regulation_keywords": ["T梁", "T形梁"],
        "is_load_bearing": True,
    },
    "箱梁": {
        "category": "上部结构",
        "aliases": [],
        "regulation_keywords": ["箱梁"],
        "is_load_bearing": True,
    },
    "钢箱梁": {
        "category": "上部结构",
        "aliases": [],
        "regulation_keywords": ["钢箱梁"],
        "is_load_bearing": True,
    },
    "空心板": {
        "category": "上部结构",
        "aliases": ["板梁", "预制板", "预制梁", "微弯板"],
        "regulation_keywords": ["空心板", "板梁", "预制板"],
        "is_load_bearing": True,
    },
    "钢梁": {
        "category": "上部结构",
        "aliases": ["钢纵梁", "钢横梁"],
        "regulation_keywords": ["钢梁", "钢纵梁", "钢横梁"],
        "is_load_bearing": True,
    },
    "钢桁梁": {
        "category": "上部结构",
        "aliases": ["钢桁架"],
        "regulation_keywords": ["钢桁梁", "桁架"],
        "is_load_bearing": True,
    },
    "横隔板": {
        "category": "上部结构",
        "aliases": ["横隔梁"],
        "regulation_keywords": ["横隔板", "横隔梁"],
        "is_load_bearing": True,
    },
    "湿接缝": {
        "category": "上部结构",
        "aliases": ["接缝"],
        "regulation_keywords": ["湿接缝", "接缝"],
        "is_load_bearing": True,
    },
    "翼板": {
        "category": "上部结构",
        "aliases": [],
        "regulation_keywords": ["翼板", "翼缘"],
    },
    "斜拉索": {
        "category": "上部结构",
        "aliases": ["拉索"],
        "regulation_keywords": ["斜拉索", "拉索", "斜拉桥"],
    },
    "主缆": {
        "category": "上部结构",
        "aliases": ["悬索"],
        "regulation_keywords": ["主缆", "悬索桥"],
    },
    "吊杆": {
        "category": "上部结构",
        "aliases": [],
        "regulation_keywords": ["吊杆"],
    },
    # R60 新增:T构桥/连续刚构悬挂段
    "挂梁": {
        "category": "上部结构",
        "aliases": ["悬挂梁", "悬臂段"],
        "regulation_keywords": ["挂梁", "悬挂段", "T构"],
        "is_load_bearing": True,
    },
    # R60 新增:空心板桥的横向连接(常出现纵裂、漏水)
    "铰缝": {
        "category": "上部结构",
        "aliases": ["板间铰缝", "纵缝"],
        "regulation_keywords": ["铰缝", "板缝", "空心板铰缝"],
        "is_load_bearing": True,
    },
    # R60 新增:拱桥拱圈
    "拱圈": {
        "category": "上部结构",
        "aliases": ["主拱圈", "拱肋"],
        "regulation_keywords": ["拱圈", "主拱圈", "拱肋", "拱桥"],
        "is_load_bearing": True,
    },

    # ─── 下部结构 ───
    "桥墩": {
        "category": "下部结构",
        "aliases": ["墩柱", "墩身", "薄壁墩", "下部结构", "下部承重构件", "下部"],
        "regulation_keywords": ["桥墩", "墩柱", "墩身", "下部结构"],
        "is_load_bearing": True,
    },
    "桥台": {
        "category": "下部结构",
        "aliases": ["台身", "台帽", "台背"],
        "regulation_keywords": ["桥台", "台身", "台帽", "台背"],
    },
    "盖梁": {
        "category": "下部结构",
        "aliases": ["墩帽", "排架"],
        "regulation_keywords": ["盖梁", "墩帽", "排架"],
        "is_load_bearing": True,
    },
    "桩基": {
        "category": "下部结构",
        "aliases": ["桩基础", "桩"],
        "regulation_keywords": ["桩基", "桩基础"],
    },
    "基础": {
        "category": "下部结构",
        "aliases": [],  # 与桩基不合并(根据上次决定)
        "regulation_keywords": ["基础"],
    },
    "承台": {
        "category": "下部结构",
        "aliases": [],
        "regulation_keywords": ["承台"],
    },
    "翼墙": {
        "category": "下部结构",
        "aliases": ["耳墙"],
        "regulation_keywords": ["翼墙", "耳墙"],
    },
    "锥坡": {
        "category": "下部结构",
        "aliases": ["护坡", "桥台护坡"],
        "regulation_keywords": ["锥坡", "护坡", "桥台护坡"],
    },
    # R60 新增:盖梁牛腿(常出现在T构桥/连续刚构里挑出支撑挂梁)
    "牛腿": {
        "category": "下部结构",
        "aliases": ["盖梁牛腿", "墩柱牛腿", "悬挑牛腿", "齿块"],
        "regulation_keywords": ["牛腿", "齿块", "悬挑构件"],
        "is_load_bearing": True,
    },
    # R60 新增:墩柱之间的横向连系梁
    # 注意: '系梁' 单字会跟主梁的 alias 冲突(主梁也叫'系梁/系杆'),
    # 所以这里只用更明确的关键词
    "横系梁": {
        "category": "下部结构",
        "aliases": ["墩间系梁", "连系梁", "墩柱系梁"],
        "regulation_keywords": ["横系梁", "连系梁", "墩间系梁"],
    },
    # R60 新增:桥头搭板(台背路桥过渡构件,跳车病害关键)
    "桥头搭板": {
        "category": "下部结构",
        "aliases": ["搭板", "过渡板"],
        "regulation_keywords": ["桥头搭板", "搭板", "过渡板"],
    },

    # ─── 支座 ───
    "支座": {
        "category": "支座",
        "aliases": [],
        "regulation_keywords": ["支座"],
    },
    "盆式支座": {
        "category": "支座",
        "aliases": ["支座-盆式"],  # 旧名兼容
        "regulation_keywords": ["盆式支座", "盆式"],
    },
    "板式橡胶支座": {
        "category": "支座",
        "aliases": ["支座-板式橡胶", "橡胶支座"],
        "regulation_keywords": ["板式橡胶支座", "板式支座", "橡胶支座"],
    },
    "球型钢支座": {
        "category": "支座",
        "aliases": ["支座-球型", "球型支座"],
        "regulation_keywords": ["球型支座", "球型钢支座"],
    },
    "抗震支座": {
        "category": "支座",
        "aliases": ["支座-抗震", "减隔震支座"],
        "regulation_keywords": ["抗震支座", "减隔震"],
    },
    "高阻尼支座": {
        "category": "支座",
        "aliases": ["支座-高阻尼"],
        "regulation_keywords": ["高阻尼支座"],
    },
    "滑板支座": {
        "category": "支座",
        "aliases": ["四氟滑板"],
        "regulation_keywords": ["滑板支座", "四氟"],
    },
    # R60 新增:支座垫石(支座下的混凝土垫块,常见破损/酥碎)
    "支座垫石": {
        "category": "支座",
        "aliases": ["垫石", "墩顶垫石", "盖梁顶垫石"],
        "regulation_keywords": ["支座垫石", "垫石"],
    },
    "挡块": {
        "category": "支座",
        "aliases": ["抗震挡块", "限位挡块"],
        "regulation_keywords": ["挡块", "抗震挡块", "限位装置"],
    },

    # ─── 桥面系 ───
    # 关键:canonical 是 "沥青铺装"(与 expert_kb 一致),不是 "桥面铺装"!
    # 用户输入的"桥面铺装/桥面板/桥面"会通过 aliases 归一化到这里
    "沥青铺装": {
        "category": "桥面系",
        "aliases": [
            "桥面铺装", "桥梁面板", "桥梁面层",
            "桥面板", "桥面系", "桥面",
            "面板", "面层", "铺装",
        ],
        "regulation_keywords": [
            "桥面", "铺装", "桥面系", "桥面铺装", "沥青混凝土面层",
        ],
        "is_deck": True,
    },
    "水泥铺装": {
        "category": "桥面系",
        "aliases": ["混凝土铺装", "水泥混凝土铺装"],
        "regulation_keywords": ["水泥", "混凝土铺装"],
        "is_deck": True,
    },
    "护栏": {
        "category": "桥面系",
        "aliases": ["栏杆"],  # 防撞墙划归到防撞护栏 (附属设施)
        "regulation_keywords": ["护栏", "栏杆", "防撞"],
    },
    "伸缩缝": {
        "category": "桥面系",
        "aliases": ["伸缩装置"],
        "regulation_keywords": ["伸缩缝", "伸缩装置"],
    },
    "排水设施": {
        "category": "桥面系",
        "aliases": ["排水管", "泄水管", "排水"],
        "regulation_keywords": ["排水", "泄水"],
    },
    "防水层": {
        "category": "桥面系",
        "aliases": [],
        "regulation_keywords": ["防水层", "防水"],
    },
    "人行道": {
        "category": "桥面系",
        "aliases": [],
        "regulation_keywords": ["人行道"],
    },

    # ─── 附属设施 ───
    "防撞护栏": {
        "category": "附属设施",
        "aliases": ["防撞墙", "防撞桶"],
        "regulation_keywords": ["防撞护栏", "防撞墙", "防撞桶"],
    },
    "标志牌": {
        "category": "附属设施",
        "aliases": ["路标"],
        "regulation_keywords": ["标志", "标志牌"],
    },
    "照明设施": {
        "category": "附属设施",
        "aliases": ["照明", "路灯"],
        "regulation_keywords": ["照明", "路灯"],
    },
    "监控摄像头": {
        "category": "附属设施",
        "aliases": ["摄像头", "监控"],
        "regulation_keywords": ["摄像头", "监控"],
    },
    "防眩板": {
        "category": "附属设施",
        "aliases": [],
        "regulation_keywords": ["防眩板"],
    },
    "声屏障": {
        "category": "附属设施",
        "aliases": [],
        "regulation_keywords": ["声屏障"],
    },
}


# ══════════════════════════════════════════════════════════
# 病害本体
# ══════════════════════════════════════════════════════════

# 同样:canonical 与 expert_kb 一致
_DISEASE_DEFS: Dict[str, Dict] = {

    # ─── 裂缝类(细分) ───
    "斜向裂缝": {
        "aliases": ["斜裂缝", "斜向裂"],
        "root": "裂缝",
        "is_load_bearing_crack": True,  # 受力缝
    },
    "纵向裂缝": {
        "aliases": ["纵裂缝", "纵裂"],
        "root": "裂缝",
        "is_load_bearing_crack": True,
    },
    "横向裂缝": {
        "aliases": ["横裂缝", "横裂"],
        "root": "裂缝",
        "is_load_bearing_crack": False,  # 横向裂缝多为收缩缝
    },
    "竖向裂缝": {
        "aliases": ["竖裂缝", "垂直裂缝"],
        "root": "裂缝",
        "is_load_bearing_crack": True,
    },
    "网状裂缝": {
        "aliases": ["龟裂", "网裂"],
        "root": "裂缝",
    },
    "铺装拥包": {
        "aliases": ["拥包", "铺装拱起"],
        "root": "变形",
        "kind": "功能病害",
    },
    "桥头跳车": {
        "aliases": ["桥头跳"],
        "root": "不平顺",
        "kind": "功能病害",
    },

    # 通用"裂缝"作为兜底
    "裂缝": {
        "aliases": ["开裂", "裂开", "有裂"],  # 删除单字"裂",避免误匹"炸裂/破裂"等
        "root": "裂缝",
    },

    # ─── 露筋 ───
    "锈胀露筋": {
        "aliases": ["钢筋锈胀"],
        "root": "露筋",
    },
    "剥落露筋": {
        "aliases": [],
        "root": "露筋",
    },
    "露筋": {
        "aliases": [],
        "root": "露筋",
    },

    # ─── 破损 ───
    "混凝土破损": {
        "aliases": ["破损", "剥落", "混凝土剥落", "酥碱", "酥松", "掉角"],
        "root": "破损",
    },
    "橡胶带破损": {
        "aliases": [
            "止水带破损", "橡胶条破损", "橡胶带开裂", "止水带开裂",
            "橡胶带脱落", "止水带脱落",
        ],
        "root": "破损",
        "kind": "功能病害",
    },
    "蜂窝麻面": {
        "aliases": ["蜂窝", "麻面", "露骨料", "漏骨料", "骨料外露", "露骨"],
        "root": "破损",
    },
    "混凝土不密实": {
        "aliases": ["不密实", "混凝土孔洞", "孔洞", "空洞"],
        "root": "破损",
    },
    "混凝土夹渣": {
        "aliases": ["砼夹渣", "夹渣", "混凝土夹杂"],
        "root": "破损",
        "kind": "施工缺陷",
    },
    "保护层过薄": {
        "aliases": ["保护层不足"],
        "root": "耐久性缺陷",
        "kind": "施工缺陷",
    },

    # ─── 支座类 ───
    "支座脱空": {
        "aliases": ["脱空"],
        "root": "脱空",
    },
    "支座偏位": {
        "aliases": ["偏位", "偏移", "支座错位", "错位", "位移转角超限"],
        "root": "偏位",
    },
    "支座偏压": {
        "aliases": ["偏压"],
        "root": "偏位",
    },
    "支座老化": {
        "aliases": ["老化", "鼓包", "轻微鼓", "支座鼓包"],
        "root": "老化",
    },
    "支座卡阻": {
        "aliases": [
            "支座抵死", "抵死", "卡死", "活动受阻", "转动受阻",
            "支座包裹", "砼包裹", "混凝土包裹",
        ],
        "root": "卡阻",
    },
    "支座未解锁": {
        "aliases": ["未解锁"],
        "root": "卡阻",
        "kind": "施工缺陷",
    },

    # ─── 锈蚀 ───
    "锈蚀": {
        "aliases": ["腐蚀", "锈", "锈迹", "生锈", "型钢锈蚀", "构件锈蚀"],
        "root": "锈蚀",
    },
    "锈胀": {
        "aliases": ["钢筋锈胀但未露筋"],
        "root": "锈蚀",
    },

    # ─── 渗水 ───
    "渗水泛碱": {
        "aliases": ["渗水", "泛碱", "渗漏", "水迹", "析白", "白渍"],
        "root": "渗水",
    },

    # ─── 桥面系特有 ───
    "坑槽": {
        "aliases": [],
        "root": "坑槽",
    },
    "伸缩缝堵塞": {
        "aliases": ["堵塞"],
        "root": "堵塞",
    },
    "伸缩缝高差": {
        "aliases": ["高差", "错台", "凹凸不平"],
        "root": "高差",
    },
    "杂物堆积": {
        "aliases": ["杂物", "垃圾堆积", "垃圾", "积土", "堆土", "建筑材料堆放"],
        "root": "堵塞",
        "kind": "环境养护",
    },
    "排水不畅": {
        "aliases": ["积水", "排水受阻", "泄水不畅"],
        "root": "排水",
        "kind": "功能病害",
    },
    "铺装磨耗": {
        "aliases": ["磨耗", "磨损"],
        "root": "磨耗",
        "kind": "功能病害",
    },
    "构件缺失": {
        "aliases": ["缺失", "缺少", "丢失", "遗失", "缺件", "未设", "未设置"],
        "root": "缺失",
        "kind": "功能病害",
    },
    "粘结失效": {
        "aliases": ["脱胶", "开胶"],
        "root": "连接失效",
    },
    "结构位移": {
        "aliases": ["滑移", "异常位移"],
        "root": "偏位",
    },
    "涂层劣化": {
        "aliases": [
            "涂层起皮", "涂层剥落", "涂层粉化", "涂层起泡", "涂装失效",
            "油漆剥落", "防腐层失效", "涂层劣化",
        ],
        "root": "防护失效",
        "kind": "耐久性病害",
    },
    "混凝土碳化": {
        "aliases": ["碳化"],
        "root": "耐久性缺陷",
    },

    # ─── 档案实测补充:环境、施工与连接类 ───
    "植被覆盖": {
        "aliases": ["长草", "杂草丛生", "植物覆盖", "植被生长"],
        "root": "附着",
        "kind": "环境养护",
    },
    "划伤": {
        "aliases": ["划痕", "刮痕", "刮伤", "剐蹭", "刮蹭", "擦伤"],
        "root": "表面损伤",
        "kind": "表观病害",
    },
    "表面污染": {
        "aliases": ["污渍", "泥渍", "污染", "沥青污染", "油污", "油渍污染", "熏黑"],
        "root": "污染",
        "kind": "环境养护",
    },
    "模板未拆除": {
        "aliases": ["模板未拆", "模版未拆", "未拆模", "挡板未拆"],
        "root": "施工遗留",
        "kind": "施工缺陷",
    },
    "跑模": {
        "aliases": ["胀模"],
        "root": "施工缺陷",
        "kind": "施工缺陷",
    },
    "脱模缺陷": {
        "aliases": ["脱模", "轻微脱模"],
        "root": "施工缺陷",
        "kind": "施工缺陷",
    },
    "钢筋未切除": {
        "aliases": ["钢筋未切", "钢筋未切断", "钢筋凸起"],
        "root": "施工遗留",
        "kind": "施工缺陷",
    },
    "螺栓松动": {
        "aliases": ["锚栓松动", "连接螺栓松动"],
        "root": "连接失效",
    },
    "连接件松动": {
        "aliases": ["橡胶条松动", "密封条松动", "管卡松动"],
        "root": "连接失效",
    },
    "构件脱开": {
        "aliases": ["排水管脱开", "线槽脱开", "接头脱开"],
        "root": "连接失效",
    },
    "结构分离": {
        "aliases": ["台身与护坡分离", "桥台与护坡分离", "结构脱离"],
        "root": "连接失效",
    },
    "空鼓": {
        "aliases": ["修补空鼓", "修补后空鼓"],
        "root": "脱空",
    },
    "桥下空间占用": {
        "aliases": ["违建", "违法搭建", "桥下养殖", "桥下居住", "广告牌覆盖"],
        "root": "空间事件",
        "kind": "环境事件",
    },
    "生物附着": {
        "aliases": ["浮游生物附着", "壳类生物附着", "水生生物附着"],
        "root": "附着",
        "kind": "环境养护",
    },
    "限位间隙异常": {
        "aliases": ["挡块间隙过小", "挡块过矮", "挡块过低"],
        "root": "限位异常",
        "kind": "功能病害",
    },
    "锚头未封闭": {
        "aliases": ["封锚端未封闭", "锚头外露"],
        "root": "防护失效",
    },
    "预应力筋外露": {
        "aliases": ["钢绞线外露", "预应力钢筋外露"],
        "root": "防护失效",
    },
    "翘曲变形": {
        "aliases": ["翘起", "钢板翘起", "翘曲"],
        "root": "变形",
    },
    "构件变形": {
        "aliases": ["局部变形"],
        "root": "变形",
    },
    "标识失效": {
        "aliases": ["反光条失效", "标线失效"],
        "root": "功能失效",
        "kind": "功能病害",
    },
    "照明失效": {
        "aliases": ["照明灯损坏", "灯具损坏"],
        "root": "功能失效",
        "kind": "功能病害",
    },
    "车辙": {
        "aliases": ["整体车辙"],
        "root": "铺装变形",
        "kind": "功能病害",
    },

    # ─── R60 补全:问答常见但之前缺失的病害 ───
    "倾斜": {
        "aliases": ["倾斜变位", "偏斜", "歪斜"],
        "root": "变形",
    },
    "冲刷": {
        "aliases": ["河床冲刷", "基础冲刷", "掏空", "冲蚀"],
        "root": "冲刷",
    },
    "断裂": {
        "aliases": ["断开", "折断", "崩断"],
        "root": "断裂",
    },
    "下挠": {
        "aliases": ["挠度过大", "永久变形", "跨中下挠"],
        "root": "变形",
    },
    "沉降": {
        "aliases": ["不均匀沉降", "基础沉降", "墩台沉降", "塌陷"],
        "root": "沉降",
    },
    "钢梁裂纹": {
        "aliases": ["钢构件裂纹", "钢板裂纹", "焊缝裂纹", "焊缝开裂", "裂纹", "焊缝"],
        "root": "裂纹",
    },
    "剪切变形": {
        "aliases": ["橡胶变形", "剪切位移", "剪切", "支座变形", "横向剪切", "纵向剪切"],
        "root": "变形",
    },
}


# ══════════════════════════════════════════════════════════
# 报告文本识别规则
# ══════════════════════════════════════════════════════════

# 规则顺序即优先级。两个报告解析器都调用 match_disease_text，避免各自维护
# 一套名称并输出互不兼容的标签。
_DISEASE_TEXT_RULES: List[Tuple[str, str]] = [
    # 裂缝方向必须先于通用裂缝。
    (r"纵向(?:受力)?裂缝|纵向开裂|纵向裂|纵裂缝|纵裂", "纵向裂缝"),
    (r"横向裂缝|横向开裂|横向裂|横裂缝|横裂", "横向裂缝"),
    (r"竖向(?:干缩)?裂缝|竖向开裂|竖向裂|垂直裂缝|竖裂缝|竖裂", "竖向裂缝"),
    (r"斜向裂缝|斜向开裂|斜向裂|斜裂缝|斜裂", "斜向裂缝"),
    (r"网状裂缝|网状开裂|龟裂|网裂", "网状裂缝"),

    # 防护、连接和施工缺陷放在“剥落/破损/外露”等宽词之前。
    (r"涂层(?:劣化|起皮|剥落|粉化|起泡)|涂装(?:失效|起皮|剥落)|油漆剥落|防腐层失效", "涂层劣化"),
    (r"封锚端?未封闭|封锚区未封完好|未封完好|锚头(?:未封闭|外露)", "锚头未封闭"),
    (r"钢绞线外露|预应力(?:筋|钢筋)外露", "预应力筋外露"),
    (r"锚栓松动|螺栓松动|连接螺栓松动", "螺栓松动"),
    (r"橡胶条.*松动|密封条.*松动|管卡松动", "连接件松动"),
    (r"排水管脱开|线槽脱开|接头脱开", "构件脱开"),
    (r"(?:桥台|台身|\d+号台|台底).*护坡.*分离|结构脱离", "结构分离"),
    (r"模板未拆|模版未拆|未拆模|挡板未拆", "模板未拆除"),
    (r"跑模|胀模", "跑模"),
    (r"轻微脱模|脱模", "脱模缺陷"),
    (r"钢筋未切(?:除|断)?|钢筋凸起", "钢筋未切除"),
    (r"保护层(?:过薄|不足)", "保护层过薄"),
    (r"(?:伸缩缝)?(?:橡胶带|橡胶条|止水带).*(?:破损|开裂|脱落|损坏)", "橡胶带破损"),

    # 混凝土与钢材表观病害。
    (r"剥落露筋", "剥落露筋"),
    (r"锈胀露筋|钢筋锈胀.*露筋", "锈胀露筋"),
    (r"锈胀", "锈胀"),
    (r"露筋|钢筋外露|主筋外露", "露筋"),
    (r"麻面露骨料|蜂窝麻面|露骨料|漏骨料|骨料外露|蜂窝|麻面|露骨", "蜂窝麻面"),
    (r"砼夹渣|混凝土夹渣|夹渣|混凝土夹杂", "混凝土夹渣"),
    (r"混凝土不密实|不密实|空洞|孔洞|夹杂", "混凝土不密实"),
    (r"修补后?空鼓|空鼓", "空鼓"),
    (r"混凝土碳化|碳化", "混凝土碳化"),
    (r"型钢锈蚀|锈蚀|腐蚀|锈迹|生锈", "锈蚀"),
    (r"掉角|剥落|掉块|碎边|崩裂|破损|破碎|酥松|酥碱|松散|脱落", "混凝土破损"),

    # 支座和限位功能。
    (r"支座未解锁|未解锁", "支座未解锁"),
    (r"支座.*(?:砼|混凝土)包裹|(?:砼|混凝土)包裹支座|支座抵死|抵死|卡死|活动受阻|转动受阻", "支座卡阻"),
    (r"支座偏压|偏压", "支座偏压"),
    (r"支座变形|剪切变形|剪切位移|(?:横向|纵向).*剪切|剪切", "剪切变形"),
    (r"支座老化|鼓包|轻微鼓", "支座老化"),
    (r"挡块.*(?:间隙过小|过矮|过低)|(?:间隙过小|挡块过矮|挡块过低)", "限位间隙异常"),

    # 功能、环境和养护问题。
    (r"磨耗|磨损", "铺装磨耗"),
    (r"高差|凹凸不平|错台", "伸缩缝高差"),
    (r"脱胶|开胶", "粘结失效"),
    (r"滑移|异常位移", "结构位移"),
    (r"植被覆盖|植被生长|长草|杂草丛生|杂草生长|植物覆盖", "植被覆盖"),
    (r"划痕|刮痕|刮伤|剐蹭|刮蹭|擦伤", "划伤"),
    (r"沥青污染|表面污染|污渍|泥渍|油污|油渍|熏黑|污染", "表面污染"),
    (r"(?:浮游生物|壳类生物|水生生物|生物).*附着", "生物附着"),
    (r"桥下.*(?:违建|养殖|居住|堆放|堆积)|(?:桥|跨|梁|墩).*下方.*(?:违建|养殖|居住|堆放|堆积)|有人居住|违建|违法搭建|广告牌覆盖|建筑材料堆放", "桥下空间占用"),
    (r"钢板翘起|翘曲|翘起", "翘曲变形"),
    (r"反光条失效|标线失效|标识.*老化", "标识失效"),
    (r"照明灯损坏|灯具损坏|照明设施失效", "照明失效"),
    (r"整体车辙|车辙", "车辙"),
    (r"(?:未设|未设置|无)(?:支座|挡块)|(?:缺失|缺少|丢失|遗失|缺件)", "构件缺失"),

    # 其他结构性病害。
    (r"冲刷|河床冲刷|基础冲刷|掏空|冲蚀", "冲刷"),
    (r"下挠|挠度过大|永久变形", "下挠"),
    (r"不均匀沉降|基础沉降|墩台沉降|沉降|下沉|塌陷", "沉降"),
    (r"倾斜变位|倾斜|偏斜|歪斜", "倾斜"),
    (r"断裂|断开|折断|崩断", "断裂"),
    (r"局部变形|变形", "构件变形"),
    (r"析白|泛碱|白渍|横向泛|渗水|漏水|水迹|水蚀|湿渍|渗漏", "渗水泛碱"),
    (r"坑槽", "坑槽"),
    (r"裂缝|开裂", "裂缝"),
]

_NORMAL_STATE_PATTERNS = [
    re.compile(r"未见.{0,12}(?:病害|异常|缺损)"),
    re.compile(r"(?:未发现|无明显|无)(?:可见|明显)?.{0,6}(?:病害|异常|缺损)"),
]

_SUPPORT_CONFIGURATION_PATTERNS = [
    re.compile(
        r"^(?:\d+号[墩台])?(?:前排|后排)?(?:\d+(?:[、,，~～至-]\d+)*)[号#]?"
        r"支座(?:为)?(?:纵向|横向|双向|固定)(?:滑动|活动)?(?:支座)?-?$"
    ),
    re.compile(
        r"^(?:\d+号[墩台])?(?:前排|后排)?(?:\d+(?:[、,，~～至-]\d+)*)[号#]?"
        r"(?:纵向|横向|双向|固定)(?:滑动|活动)?支座-?$"
    ),
]


def is_non_disease_description(text: str) -> bool:
    """识别正常结论和支座布置信息，防止它们进入病害评分。"""
    normalized = re.sub(r"[\s，,。；;：:]", "", str(text or ""))
    if not normalized:
        return False
    if normalized in {"正常", "良好", "完好", "无病害", "无异常", "未见异常"}:
        return True
    if normalized.endswith(("完好", "通畅")) and not normalized.endswith(("未封完好", "不完好")):
        return True
    if any(pattern.search(normalized) for pattern in _NORMAL_STATE_PATTERNS):
        return True
    support_text = re.sub(r"号(?=[、,，])", "", normalized)
    return any(pattern.fullmatch(support_text) for pattern in _SUPPORT_CONFIGURATION_PATTERNS)


def match_disease_text(text: str, component_hint: str = "") -> Tuple[Optional[str], str]:
    """从报告短语中返回 (统一病害名, 命中的原文词)，无命中返回 (None, "")."""
    text = str(text or "").strip()
    if not text or is_non_disease_description(text):
        return None, ""

    combined = f"{component_hint} {text}"

    # 这些词需要结合构件语境，不能只靠一个宽泛别名判断。
    m = re.search(r"桥下.*(?:违建|养殖|居住|堆放|堆积)|(?:桥|跨|梁|墩).*下方.*(?:违建|养殖|居住|堆放|堆积)|有人居住|违建|建筑材料堆放", text)
    if m:
        return "桥下空间占用", m.group(0)

    m = re.search(r"积土|堆土|垃圾(?:堆积)?|杂物(?:堆积)?|建筑材料堆放", text)
    if m:
        name = "伸缩缝堵塞" if "伸缩缝" in combined or "伸缩装置" in combined else "杂物堆积"
        return name, m.group(0)

    m = re.search(r"积水|排水不畅|排水受阻|泄水不畅", text)
    if m:
        return "排水不畅", m.group(0)

    m = re.search(r"堵塞", text)
    if m:
        drain_context = any(word in combined for word in ("排水", "泄水", "雨篦", "格栅"))
        return ("排水不畅" if drain_context else "伸缩缝堵塞"), m.group(0)

    m = re.search(r"偏位|偏移|错位|位移[、,，/]?转角超限", text)
    if m:
        return "支座偏位", m.group(0)

    m = re.search(r"位移", text)
    if m:
        comp = canonicalize_component(component_hint) or component_hint
        is_support = "支座" in text or comp in {
            "支座", "盆式支座", "板式橡胶支座", "球型钢支座", "抗震支座",
            "高阻尼支座", "滑板支座",
        }
        return ("支座偏位" if is_support else "结构位移"), m.group(0)

    m = re.search(r"脱空", text)
    if m:
        comp = canonicalize_component(component_hint) or component_hint
        return ("支座脱空" if comp in {
            "支座", "盆式支座", "板式橡胶支座", "球型钢支座", "抗震支座",
            "高阻尼支座", "滑板支座",
        } or "支座" in text else "空鼓"), m.group(0)

    for pattern, canonical in _DISEASE_TEXT_RULES:
        m = re.search(pattern, text)
        if m:
            return canonical, m.group(0)
    return None, ""


def classify_disease_text(text: str, component_hint: str = "") -> Optional[str]:
    """match_disease_text 的病害名简写接口。"""
    return match_disease_text(text, component_hint)[0]


# ══════════════════════════════════════════════════════════
# 索引(在 import 时构建,用 dict 加速 O(1) 查询)
# ══════════════════════════════════════════════════════════

# alias → canonical 的反向索引(包含 canonical 自己)
_COMP_ALIAS_TO_CANONICAL: Dict[str, str] = {}
for canonical, info in _COMPONENT_DEFS.items():
    _COMP_ALIAS_TO_CANONICAL[canonical] = canonical
    for alias in info.get("aliases", []):
        _COMP_ALIAS_TO_CANONICAL[alias] = canonical

_DISEASE_ALIAS_TO_CANONICAL: Dict[str, str] = {}
for canonical, info in _DISEASE_DEFS.items():
    _DISEASE_ALIAS_TO_CANONICAL[canonical] = canonical
    for alias in info.get("aliases", []):
        _DISEASE_ALIAS_TO_CANONICAL[alias] = canonical

# canonical → category 索引
_CANONICAL_TO_CATEGORY: Dict[str, str] = {
    c: info.get("category", "") for c, info in _COMPONENT_DEFS.items()
}


# ══════════════════════════════════════════════════════════
# 公开 API
# ══════════════════════════════════════════════════════════

def canonicalize_component(name: str) -> Optional[str]:
    """
    把任意构件名(canonical/alias/口语)归一化到 canonical name。
    返回 None 表示不在本体里。

    例:
      canonicalize_component("桥面铺装") → "沥青铺装"
      canonicalize_component("墩柱")     → "桥墩"
      canonicalize_component("主梁")     → "主梁"
      canonicalize_component("不存在")   → None
    """
    if not name:
        return None
    return _COMP_ALIAS_TO_CANONICAL.get(name.strip())


def canonicalize_disease(name: str) -> Optional[str]:
    """病害版本"""
    if not name:
        return None
    return _DISEASE_ALIAS_TO_CANONICAL.get(name.strip())


def fuzzy_canonicalize_component(name: str) -> Optional[str]:
    """
    模糊版:精确匹配失败时,按"包含关系 + 最长 alias"做兜底。
    用于用户自由文本,例如 "桥梁面层" 不在精确表里但能匹到 "面层"。
    """
    if not name:
        return None
    name = name.strip()
    # 精确
    if name in _COMP_ALIAS_TO_CANONICAL:
        return _COMP_ALIAS_TO_CANONICAL[name]
    # 模糊:在 alias 集合里找最长的子串匹配
    best_alias = None
    best_len = 0
    for alias in _COMP_ALIAS_TO_CANONICAL:
        if len(alias) < 2:
            continue
        if alias in name and len(alias) > best_len:
            best_alias = alias
            best_len = len(alias)
    if best_alias:
        return _COMP_ALIAS_TO_CANONICAL[best_alias]

    # 检测报告经常把下部结构写成实例编号，而不是本体词：
    # “0号台”“2#墩”“左幅P3号墩”。只有在更具体的构件别名均未命中后
    # 才走该兜底，避免把“0号台前排2号支座”误归为桥台。
    instance_no = r"(?:[A-Za-z]?\d+(?:[-~～—]\d+)?|[一二三四五六七八九十百]+)"
    if re.search(instance_no + r"\s*(?:号|#)?\s*(?:桥)?墩(?:柱|身)?(?:\s|$|[左右侧前后上下游幅])", name):
        return "桥墩"
    if re.search(instance_no + r"\s*(?:号|#)?\s*(?:桥)?台(?:身|帽|背)?(?:\s|$|[左右侧前后上下游幅])", name):
        return "桥台"
    return None


def infer_category(comp_name: str) -> Optional[str]:
    """从任意构件名推断大类"""
    canonical = canonicalize_component(comp_name) or fuzzy_canonicalize_component(comp_name)
    if canonical:
        return _CANONICAL_TO_CATEGORY.get(canonical)
    return None


def get_aliases(canonical: str) -> Set[str]:
    """获取一个 canonical 的所有别名(包括它自己)"""
    canonical = canonicalize_component(canonical) or canonical
    if canonical not in _COMPONENT_DEFS:
        return set()
    return {canonical, *_COMPONENT_DEFS[canonical].get("aliases", [])}


def get_regulation_keywords(canonical: str) -> List[str]:
    """获取规范文本匹配用的关键词族"""
    canonical = canonicalize_component(canonical) or canonical
    if canonical not in _COMPONENT_DEFS:
        return [canonical] if canonical else []
    return _COMPONENT_DEFS[canonical].get("regulation_keywords", [canonical])


def are_equivalent_components(c1: str, c2: str) -> bool:
    """
    判断两个构件名是否指向同一物理对象。
    这是治本 db 查询的关键 API:即使 c1='桥面铺装' c2='沥青铺装' 也能正确返回 True。
    """
    can1 = canonicalize_component(c1) or c1
    can2 = canonicalize_component(c2) or c2
    return can1 == can2


def are_equivalent_diseases(d1: str, d2: str) -> bool:
    """病害版本"""
    can1 = canonicalize_disease(d1) or d1
    can2 = canonicalize_disease(d2) or d2
    return can1 == can2


# 语义查询:某构件是否承重 / 是否桥面
def is_load_bearing_component(comp_name: str) -> bool:
    canonical = canonicalize_component(comp_name) or fuzzy_canonicalize_component(comp_name)
    if not canonical:
        return False
    return _COMPONENT_DEFS.get(canonical, {}).get("is_load_bearing", False)


def is_deck_component(comp_name: str) -> bool:
    canonical = canonicalize_component(comp_name) or fuzzy_canonicalize_component(comp_name)
    if not canonical:
        return False
    return _COMPONENT_DEFS.get(canonical, {}).get("is_deck", False)


def is_load_bearing_crack(disease_name: str) -> bool:
    """病害是否为受力裂缝(影响 ClarificationAgent 的反问)"""
    canonical = canonicalize_disease(disease_name) or disease_name
    return _DISEASE_DEFS.get(canonical, {}).get("is_load_bearing_crack", False)


def get_disease_root(disease_name: str) -> Optional[str]:
    """获取病害的词根(用于 db 模糊匹配,如'斜向裂缝'→'裂缝')"""
    canonical = canonicalize_disease(disease_name) or disease_name
    return _DISEASE_DEFS.get(canonical, {}).get("root")


# 给老接口的兼容性导出:把 ontology 渲染成各模块需要的格式
def build_comp_keywords_list() -> List[tuple]:
    """
    为 app.py · _COMP_KEYWORDS 渲染:
      [(alias, canonical), ...] 按 alias 长度倒序(避免短串截胡)
    """
    pairs = []
    for canonical, info in _COMPONENT_DEFS.items():
        # canonical 自己也加上(以防别处用 canonical 直接查)
        pairs.append((canonical, canonical))
        for alias in info.get("aliases", []):
            pairs.append((alias, canonical))
    # 长串在前,避免"桥面"截胡"桥面铺装"
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


def build_disease_keywords_list() -> List[tuple]:
    """病害版本"""
    pairs = []
    for canonical, info in _DISEASE_DEFS.items():
        pairs.append((canonical, canonical))
        for alias in info.get("aliases", []):
            pairs.append((alias, canonical))
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


def build_category_map() -> Dict[str, str]:
    """
    为 decision_engine 渲染:
      所有 (canonical 和 alias) → category
    """
    out = {}
    for canonical, info in _COMPONENT_DEFS.items():
        cat = info.get("category", "")
        out[canonical] = cat
        for alias in info.get("aliases", []):
            out[alias] = cat
    return out


def build_kg_component_keywords() -> Dict[str, List[str]]:
    """
    为 knowledge_graph / RegulationRAGAgent 渲染:
      {canonical: [regulation_keywords, ...]}
    用于规范条文文本匹配
    """
    return {
        canonical: info.get("regulation_keywords", [canonical])
        for canonical, info in _COMPONENT_DEFS.items()
    }


def list_canonical_components() -> List[str]:
    """所有 canonical 构件名"""
    return list(_COMPONENT_DEFS.keys())


def list_canonical_diseases() -> List[str]:
    """所有 canonical 病害名"""
    return list(_DISEASE_DEFS.keys())


def list_load_bearing_components() -> Set[str]:
    """所有承重构件 canonical 名"""
    return {
        c for c, info in _COMPONENT_DEFS.items()
        if info.get("is_load_bearing", False)
    }


def list_deck_components() -> Set[str]:
    """所有桥面类构件 canonical 名(也包括它们的 alias)"""
    out = set()
    for canonical, info in _COMPONENT_DEFS.items():
        if info.get("is_deck", False):
            out.add(canonical)
            out.update(info.get("aliases", []))
    return out


# ══════════════════════════════════════════════════════════
# 自检
# ══════════════════════════════════════════════════════════

def self_check():
    """启动时自检:确保 ontology 内部一致"""
    issues = []

    # 1. canonical 不能同时出现在另一个 canonical 的 aliases 里
    for canonical, info in _COMPONENT_DEFS.items():
        for other_canonical, other_info in _COMPONENT_DEFS.items():
            if canonical == other_canonical:
                continue
            if canonical in other_info.get("aliases", []):
                issues.append(
                    f"⚠️  '{canonical}' 既是 canonical 又是 '{other_canonical}' 的 alias"
                )

    # 2. 同一 alias 不能映射到多个 canonical
    seen = {}
    for canonical, info in _COMPONENT_DEFS.items():
        for alias in info.get("aliases", []):
            if alias in seen and seen[alias] != canonical:
                issues.append(
                    f"⚠️  alias '{alias}' 同时映射到 '{seen[alias]}' 和 '{canonical}'"
                )
            seen[alias] = canonical

    # 3. 病害 alias 同样不能映射到多个 canonical。
    disease_seen = {}
    for canonical, info in _DISEASE_DEFS.items():
        for alias in info.get("aliases", []):
            if alias in disease_seen and disease_seen[alias] != canonical:
                issues.append(
                    f"⚠️  病害 alias '{alias}' 同时映射到 "
                    f"'{disease_seen[alias]}' 和 '{canonical}'"
                )
            disease_seen[alias] = canonical

    if issues:
        print("⚠️  Ontology 自检发现问题:")
        for i in issues:
            print(f"  {i}")
    return issues


# 启动时跑一次自检
_issues = self_check()
if not _issues:
    print(f"📚 Ontology 已加载: {len(_COMPONENT_DEFS)} 构件 + {len(_DISEASE_DEFS)} 病害(自检通过)")
