"""
IdentityGuard · 身份锁(通用)
==============================

解决的问题:
  用户在多轮对话里突然跑题,比如"写一首诗""推荐餐厅""我是警察"...
  LLM 会被带偏,生成大量无关内容,破坏垂域专业性。

提纯自原型系统 的两层身份锁:
  - app.py 入口层的 _OFF_TOPIC_SIGNALS 硬编码词表
  - orchestrator 中的 _try_identity_lock_reject

新架构把这两层合并,且完全依赖 DomainProfile:
  - 通用拦截词表(创作类、闲聊、非本域领域词)放在 nskernel 里
  - 领域专有词(本域允许/禁止)放在 DomainProfile.identity 里
  - 回复模板(礼貌拒绝)从 DomainProfile.identity.off_topic_reply 读

组件:
  1. OffTopicDetector - 识别跑题信号
     - 创作类检测(写诗/小说/故事 + regex 覆盖量词变体)
     - 闲聊检测(你好啊/讲笑话/吃饭了吗)
     - 非本域领域词检测
  2. IdentityLockGuard - 身份锁主入口
     - 判定是否跑题
     - 从 DomainProfile 生成拒绝回复
     - 返回"应拦截"或"放行"的决策
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from nskernel.contracts import DomainProfile


# ══════════════════════════════════════════════════════════════
# 通用跑题信号词表(跨所有领域共享)
# ══════════════════════════════════════════════════════════════

# 职业身份声明(模拟用户试图用"身份"影响 AI)
# 纯打招呼/问候 - 应该友好回应,不算跑题
_UNIVERSAL_GREETINGS = (
    "你好", "您好", "hi", "hello", "hey", "哈喽",
    "嗨", "早", "晚上好", "下午好", "中午好",
    "在不在", "在的", "辛苦了", "谢谢", "谢啦",
    "感谢", "thanks", "thank you",
    "好的", "嗯嗯", "好", "ok", "OK",
    "可以", "明白了", "懂了", "了解",
    "不错", "厉害", "牛", "牛逼",
)


def is_greeting(q: str) -> bool:
    """判断是否纯打招呼"""
    if not q: return False
    q_clean = q.strip().rstrip("。.!?!??,,").lower()
    if len(q_clean) > 8:  # 太长不算 greeting
        return False
    return any(g.lower() == q_clean or g.lower() in q_clean
               for g in _UNIVERSAL_GREETINGS)


_UNIVERSAL_CHITCHAT_PATTERNS = (
    "我是警察", "我是老师", "我是医生", "我是学生",
    "我是老板", "我是工人", "我是记者", "我是公务员",
    "吃饭了吗", "吃过了", "早上好", "晚安", "你几岁",
    "讲个笑话", "讲笑话", "逗我", "逗逗我",
    "你是谁", "你叫什么", "你多大了", "今天天气", "几点了",
    "陪我聊", "无聊", "在吗",
)

# 情感/人际关系类(大部分垂域里算跑题,但医疗/心理咨询里可能算域内)
# 所以这类词默认放在通用词表,领域如果"自己需要讨论这些",
# 就在 DomainProfile.identity.in_scope_keywords 里包含对应词,即可豁免
_UNIVERSAL_EMOTIONAL_PATTERNS = (
    "吵架", "分手", "失恋", "离婚后",
    "男朋友", "女朋友", "老婆", "老公", "媳妇",
    "家里矛盾", "父母关系",
)

# 创作类关键词
_UNIVERSAL_CREATIVE_WORDS = (
    "写小说", "写个小说", "科幻小说", "小说",
    "写故事", "编个故事", "讲个故事", "故事",
    "写诗", "写首诗", "诗歌", "作诗", "之歌", "写首歌", "写歌",
    "一首诗", "一首歌", "写篇诗", "写篇歌", "一段诗",
    "写剧本", "剧本", "写散文", "散文", "写作文", "作文",
    "写推文", "公众号", "写新闻稿", "写通讯",
    "营销文案", "广告词", "写口号", "SEO",
    "演讲稿", "PPT 文案", "PPT文案",
    "虚构", "编一段", "编一个",
)

# 创作类 regex 兜底(覆盖"写 X 首 Y 诗"这类量词变体)
_UNIVERSAL_CREATIVE_REGEX = re.compile(
    r"写[一二三四五六七八九十\d]*[首篇段个部]?"
    r"[^。,,\s]{0,6}(诗|歌|小说|故事|散文|剧本|作文)"
)

# 跨领域非本域词(一出现就是跑出本域,具体哪些是领域自己决定)
_UNIVERSAL_NON_DOMAIN_WORDS = (
    "帮我翻译", "帮我润色", "帮我总结",
    "写代码", "写个程序", "写段代码", "写脚本",
    "教我", "学习编程", "Python", "JavaScript", "Java",
    "情感咨询", "情感陪伴",
    # 日常话题(大部分垂域都不需要)
    "做菜", "做饭", "炒蛋", "炒菜", "烹饪", "菜谱", "食谱",
    "餐厅", "美食", "好吃", "饭店",
    "股市", "股票", "炒股", "基金", "理财", "投资建议", "买什么股",
    "彩票", "彩蛋", "中奖",
    "电影", "看电影", "演员", "明星",
    "游戏", "玩游戏", "打游戏",
    "健身", "减肥", "瘦身",
    "旅游", "旅行", "景点",
)


# ══════════════════════════════════════════════════════════════
# 决策数据结构
# ══════════════════════════════════════════════════════════════
@dataclass
class OffTopicDetection:
    """跑题检测结果"""
    is_off_topic: bool                       # 最终判定
    reasons: List[str]                       # 判定理由("创作类" "闲聊" 等)
    matched_tokens: List[str]                # 命中的具体词

    # 子类别标记
    is_creative: bool = False                # 创作类
    is_chitchat: bool = False                # 闲聊
    is_non_domain: bool = False              # 非本域


@dataclass
class GuardDecision:
    """身份锁的最终决策"""
    should_block: bool                       # 是否应拦截
    reply_text: str = ""                     # 如果拦截,用这段话回复
    reason: str = ""                         # 日志用
    detection: Optional[OffTopicDetection] = None


# ══════════════════════════════════════════════════════════════
# OffTopicDetector - 通用跑题检测器
# ══════════════════════════════════════════════════════════════
class OffTopicDetector:
    """
    通用跑题检测器(基于词表+regex,领域无关)

    可以注入 DomainProfile 做领域定制:
      - 领域自带的 in_scope_keywords 出现 → 绝不判跑题
      - 领域自带的 out_of_scope_keywords 出现 → 直接判跑题
    """

    # 短句阈值:长度 ≤ N 且完全不含领域词 → 判跑题
    # v0.2:从 10 降到 6 - 更克制,避免误伤"我被XX能告吗"这类正常短问题
    SHORT_SENTENCE_THRESHOLD = 6

    def detect(
        self,
        question: str,
        domain_profile: Optional["DomainProfile"] = None,
    ) -> OffTopicDetection:
        """
        综合跑题检测

        判定规则(任一命中即 off_topic):
          1. 创作类:词表 或 regex
          2. 闲聊:词表
          3. 非本域:通用词表 + 领域自带 out_of_scope_keywords
          4. 短句+零领域词:长度 ≤ 阈值 且不含 in_scope_keywords
        """
        q = question or ""
        q_lower = q.lower()
        reasons: List[str] = []
        matched: List[str] = []

        # 🔧 v0.7: 纯打招呼/感谢类 - 不拦,让 LLM 自然回应
        # ("你好"/"谢谢"/"OK" 这种被身份锁拦掉太僵硬)
        if is_greeting(q):
            return OffTopicDetection(
                is_off_topic=False,
                reasons=["greeting"],
                matched_tokens=[q.strip()],
                is_creative=False,
                is_chitchat=True,
                is_non_domain=False,
            )

        # 🔧 v0.7.1: 反问/确认类短句 - 不拦,让上下文带过去
        # ("真的吗?"/"确定?"/"是吗?"/"对吗?" 这种纯反问应该让 LLM 接上文)
        challenge_patterns = ("真的吗", "真的?", "确定吗", "确定?",
                               "是吗", "对吗", "是这样", "是这个",
                               "就这些", "就这样", "都是", "全是",
                               "不是吧", "啊?", "嗯?", "为啥",)
        q_clean = q.strip().rstrip("?。.!?!?")
        if (len(q.strip()) <= 12 and 
            any(p in q for p in challenge_patterns)):
            return OffTopicDetection(
                is_off_topic=False,
                reasons=["challenge_followup"],
                matched_tokens=[q.strip()],
                is_creative=False,
                is_chitchat=False,
                is_non_domain=False,
            )

        # 领域词判定(提前跳出:领域词存在时永不判跑题)
        in_scope_words = []
        if domain_profile and domain_profile.identity:
            in_scope_words = list(domain_profile.identity.in_scope_keywords or [])
        has_in_scope = any(w in q for w in in_scope_words) if in_scope_words else False

        # 1) 创作类(即使有领域词也可能是创作请求)
        creative_hits = [w for w in _UNIVERSAL_CREATIVE_WORDS if w in q]
        is_creative = bool(creative_hits) or bool(_UNIVERSAL_CREATIVE_REGEX.search(q))
        if is_creative:
            reasons.append("创作类")
            matched.extend(creative_hits)

        # 2) 闲聊(领域词存在时闲聊也应拦,除非问题本身就是问领域问题)
        chitchat_hits = [p for p in _UNIVERSAL_CHITCHAT_PATTERNS if p in q]

        # 情感类判定:3 个豁免路径
        emotional_hits = [p for p in _UNIVERSAL_EMOTIONAL_PATTERNS if p in q]
        emotional_in_scope_markers = ("情感", "心理", "情绪", "夫妻", "婚姻", "家庭关系")
        domain_wants_emotional = any(
            m in w for w in in_scope_words for m in emotional_in_scope_markers
        )
        # 只在以下情况把情感词算作跑题:
        #   - 领域在 in_scope 里没有把"情感/心理"明确纳入
        #   - 而且问题里也没有其他 in_scope 关键词(保护"我老公高血压"这种情况)
        if emotional_hits and not domain_wants_emotional and not has_in_scope:
            chitchat_hits.extend(emotional_hits)

        is_chitchat = bool(chitchat_hits)
        if is_chitchat:
            reasons.append("闲聊")
            matched.extend(chitchat_hits)

        # 3) 非本域词
        non_domain_hits = [w for w in _UNIVERSAL_NON_DOMAIN_WORDS if w in q]
        # 领域自带的 out_of_scope_keywords 也算
        if domain_profile and domain_profile.identity:
            domain_non = domain_profile.identity.out_of_scope_keywords or []
            for w in domain_non:
                if w in q:
                    non_domain_hits.append(w)
        is_non_domain = bool(non_domain_hits)
        if is_non_domain:
            reasons.append("非本域")
            matched.extend(non_domain_hits)

        # 4) 短句+零领域词
        if (not is_creative and not is_chitchat and not is_non_domain
                and len(q) <= self.SHORT_SENTENCE_THRESHOLD
                and not has_in_scope):
            reasons.append("短句无领域词")

        is_off_topic = bool(reasons)

        return OffTopicDetection(
            is_off_topic=is_off_topic,
            reasons=reasons,
            matched_tokens=matched,
            is_creative=is_creative,
            is_chitchat=is_chitchat,
            is_non_domain=is_non_domain,
        )


# ══════════════════════════════════════════════════════════════
# IdentityLockGuard - 身份锁主接口
# ══════════════════════════════════════════════════════════════
class IdentityLockGuard:
    """
    身份锁主入口

    用法:
      guard = IdentityLockGuard(law_profile)
      decision = guard.check("写首诗吧")
      if decision.should_block:
          return decision.reply_text
      else:
          # 正常路由

    可选联动 EntityScheme:如果领域的 EntityScheme 能从问题里识别到合法实体名
    (案号/患者 ID/学号等专用格式),就算其他地方看不出领域关键词,也应放行。

    v0.2 加强:
      - 第一道防线:PromptInjectionDetector(注入攻击拦截)
        优先级最高,命中注入即拦,不走豁免路径
    """

    def __init__(
        self,
        domain_profile: "DomainProfile",
        detector: Optional[OffTopicDetector] = None,
        entity_scheme: Optional[Any] = None,
        enable_injection_defense: bool = True,
    ):
        self._profile = domain_profile
        self._detector = detector or OffTopicDetector()
        self._entity_scheme = entity_scheme
        # v0.2:prompt injection 防御(领域无关)
        self._injection_detector = None
        if enable_injection_defense:
            try:
                from nskernel.guardrails import PromptInjectionDetector
                self._injection_detector = PromptInjectionDetector()
            except ImportError:
                self._injection_detector = None

    def check(self, question: str) -> GuardDecision:
        """
        判定是否应拦截该问题
        返回 GuardDecision(should_block, reply_text, reason)
        """
        # ─── 第 0 道:Prompt injection 防御 ───
        # 注入攻击优先级最高,任何豁免路径都不能绕过
        if self._injection_detector:
            inj = self._injection_detector.detect(question or "")
            if inj.is_injection:
                return GuardDecision(
                    should_block=True,
                    reply_text=(
                        f"检测到指令注入(类型: {inj.category})。"
                        f"我只处理正常的{self._profile.identity.scope_description}问题。"
                    ) if self._profile.identity else
                    "检测到可疑指令注入,已拦截。",
                    reason=f"prompt_injection[{inj.category}]: {inj.matched_text}",
                    detection=None,
                )

        detection = self._detector.detect(question, self._profile)

        # 豁免路径:如果 EntityScheme 能从问题里识别到合法实体格式
        # (案号/病案号/学号等),即使是短句无领域词也放行
        if detection.is_off_topic and self._entity_scheme is not None:
            # 有创作词时不豁免(比如"写一首关于劳动案的诗")
            if not detection.is_creative:
                try:
                    from nskernel.contracts import DetectionResult
                    result = self._entity_scheme.detect_active_entity(question, [])
                    if result.not_found_mentioned:
                        return GuardDecision(
                            should_block=False,
                            reason=f"实体格式豁免: {result.not_found_mentioned}",
                            detection=detection,
                        )
                except Exception:
                    pass

        if not detection.is_off_topic:
            return GuardDecision(
                should_block=False,
                detection=detection,
            )

        # 根据跑题子类别选择回复模板
        if detection.is_creative:
            reply = self._profile.get_creative_reply()
        else:
            reply = self._profile.get_off_topic_reply()

        return GuardDecision(
            should_block=True,
            reply_text=reply,
            reason=",".join(detection.reasons) + "; 命中词: " + ",".join(detection.matched_tokens),
            detection=detection,
        )
