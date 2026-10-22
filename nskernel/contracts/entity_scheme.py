"""
EntityScheme · 实体方案契约
============================

回答一个问题:**在本领域,什么叫"一个东西"?**

场景 1:一个实体(如一座建筑)(实体 key = 某种标识)
场景 2: 一个案件(实体 key = 案号 如 "(2024)京0105民初12345号")
场景 3: 一个学生(实体 key = 学号 如 "S2024001")
场景 4: 一个患者(实体 key = 病案号)
场景 5: 一个账户/组合(实体 key = 账户号)

EntityScheme 决定:
  1. 如何从一条观察数据里算出它归属哪个实体(extract_entity_key)
  2. 如何从用户问题里推断指代的实体(detect_active_entity)
     - 显式提名("实体 A 相关") → 精确匹配
     - 指代词("这个案子") → 最近实体
     - 唯一实体 → 就是它
     - 多实体无指代 → 返回 None

这是 Entity-Scoped Context Projection 的接口契约。
nskernel/cognition/projection/ 里的通用投影算法,
靠 EntityScheme 做领域定制。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EntityRef:
    """对一个实体的引用(通用数据结构)"""
    key: str                                # 唯一标识("海口" / "S2024001")
    display_name: str = ""                  # 用户可读("(display_name 的样例)" / "某个体 A")
    metadata: Dict[str, Any] = field(default_factory=dict)
                                            # 额外信息(首次出现时间、观察数等)


@dataclass
class DetectionResult:
    """实体识别的结果"""
    entity_key: Optional[str]               # 命中的 key,None=未识别到
    source: str = ""                        # 'explicit' / 'reference' / 'unique' / 'none'
                                            # explicit = 用户明确提名
                                            # reference = 用了"这个/那个"等指代词
                                            # unique = 只有一个实体
                                            # none = 多实体但无指代
    not_found_mentioned: Optional[str] = None
                                            # 用户提了一个不存在的实体名
                                            # 例如用户说"某个实体"但累积里没有长沙
                                            # 上层应该明确告知"没有该数据",不默默答别的


class EntityScheme(ABC):
    """
    实体方案抽象基类

    领域实现示例(伪代码):
      class BridgeEntityScheme(EntityScheme):
          entity_label = "实体"           # 用户看到的名称
          reference_words = ["这个X", "那个X", "这件 Y"]

          def extract_entity_key(self, observation):
              # 从观察记录里取城市
              city = observation.get("城市") or observation.get("环境", {}).get("匹配城市")
              return normalize_city(city)

          def get_known_entity_names(self):
              # 内置可识别的实体名列表(城市名/案件号格式/学号格式)
              return ALL_CITIES

      class LawEntityScheme(EntityScheme):
          entity_label = "案件"
          reference_words = ["这个案子", "那个案件", "本案"]

          def extract_entity_key(self, observation):
              return observation.get("案号")

          def get_known_entity_names(self):
              # 用正则判断案号格式
              return case_number_pattern_matcher
    """

    # 领域用于显示的实体名("某业务" / "案件" / "学生")
    entity_label: str = "实体"

    # 本领域常用的指代词
    # ("这个 X / 那个 X" / "这个案子/那个案件" / "这位同学/那位同学")
    reference_words: List[str] = ()

    @abstractmethod
    def extract_entity_key(self, observation: Dict[str, Any]) -> Optional[str]:
        """
        从一条观察数据里抽取它归属的实体 key。
        返回 None 表示这条数据没绑定到任何实体。

        某业务实现: 取 city 字段归一化
        法律实现: 取 case_number 字段
        教学实现: 取 student_id 字段
        """
        ...

    @abstractmethod
    def detect_active_entity(
        self,
        question: str,
        available_entities: List[EntityRef],
    ) -> DetectionResult:
        """
        从用户问题+已知实体列表里判断:
          这次追问指向哪个实体?

        available_entities: 按"最近出现"倒序排列的已知实体
        返回 DetectionResult(包含 entity_key 和识别来源)

        识别优先级(通用,但各领域可微调):
          1. 问题显式提到某实体的名字(必须匹配 available 里的 key)
          2. 问题提了一个**可识别的实体名但不在 available 里** → not_found
          3. 问题含 reference_words → 最近一个 available 实体
          4. available 只有一个 → 就是它
          5. 其他 → None
        """
        ...

    def normalize_entity_key(self, raw_key: str) -> str:
        """
        归一化 key:
          某业务把"海口市"归一化成"海口";
          法律把"(2024)京0105民初 12345 号"去除空格;
          教学把学号补齐前导 0。

        默认不做处理,领域按需重写。
        """
        return raw_key.strip() if raw_key else raw_key

    def describe_entity(self, entity: EntityRef) -> str:
        """
        把一个实体描述成人类可读的一句话。
        默认: "{entity_label}: {display_name}"
        领域可重写("(领域自定义描述)")
        """
        name = entity.display_name or entity.key
        return f"{self.entity_label}: {name}"
