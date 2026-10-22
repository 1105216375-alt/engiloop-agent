"""
Session · 通用会话管理
=======================

一次对话的状态容器,领域无关。

职责:
  1. 保留对话历史(user/assistant messages)
  2. 累积 Observations(跨多轮,支持多实体并存)
  3. 维护轻量级上下文(当前激活实体、最近诊断结果)
  4. 提供 SessionStore 做多会话 LRU 管理

对比原型系统 提纯过程:
  - 旧版 memory 把多实体投影逻辑硬编码在 ChatSession 里
  - 新版 Session 只负责"装数据",投影走 ContextProjector
  - 新版不含"bridge_key_of"之类的领域方法,全靠 EntityScheme 注入
"""
from __future__ import annotations
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from nskernel.contracts import Observation


# ══════════════════════════════════════════════════════════════
# ChatMessage - 一条对话消息
# ══════════════════════════════════════════════════════════════
@dataclass
class ChatMessage:
    """会话里的一条消息"""
    role: str                                   # 'user' / 'assistant' / 'system'
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
# Session - 会话基类
# ══════════════════════════════════════════════════════════════
class Session:
    """
    通用会话状态容器

    关键设计:
      - 领域无关。Observation 是统一契约,不关心具体内容
      - 不做投影本身(那是 ContextProjector 的事),只管存
      - 支持多轮累积:累积全部 observations,不做强制覆盖
        换实体=不同 observation.entity_key,互不干扰
    """

    # 消息历史摘要截断长度(防 token 爆炸)
    MESSAGE_SUMMARY_LIMIT = 500
    # 历史消息最多保留多少条(超过后截取最近的)
    HISTORY_MAX_MESSAGES = 40

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.created_at = time.time()
        self.last_active = time.time()

        # 对话历史
        self.messages: List[ChatMessage] = []

        # 跨多轮累积的观察(可来自多个实体)
        self.accumulated_observations: List["Observation"] = []

        # 最近一轮的观察快照(近因追问用)
        self.last_round_observations: List["Observation"] = []

        # 会话级轻量上下文(可以存一些用户偏好、当前激活实体等)
        self.context: Dict[str, Any] = {}

        # 上一轮未决的反问(子类可用)
        self.pending_clarification: Optional[Dict[str, Any]] = None

        # 扩展字段(领域可自己放东西)
        self.metadata: Dict[str, Any] = {}

    # -----------------------------------------------------------------
    # 消息管理
    # -----------------------------------------------------------------
    def add_user_message(self, content: str, **meta) -> ChatMessage:
        msg = ChatMessage(role="user", content=content, metadata=meta)
        self.messages.append(msg)
        self.last_active = time.time()
        self._trim_history()
        return msg

    def add_assistant_message(self, content: str, **meta) -> ChatMessage:
        # 摘要截断
        summary = content if len(content) <= self.MESSAGE_SUMMARY_LIMIT \
            else content[:self.MESSAGE_SUMMARY_LIMIT] + "..."
        msg = ChatMessage(role="assistant", content=summary, metadata=meta)
        self.messages.append(msg)
        self.last_active = time.time()
        self._trim_history()
        return msg

    def _trim_history(self):
        """超过上限时只保留最新 N 条"""
        if len(self.messages) > self.HISTORY_MAX_MESSAGES:
            self.messages = self.messages[-self.HISTORY_MAX_MESSAGES:]

    def last_user_message(self) -> Optional[str]:
        for m in reversed(self.messages):
            if m.role == "user":
                return m.content
        return None

    # -----------------------------------------------------------------
    # Observation 管理
    # -----------------------------------------------------------------
    def save_observations(
        self,
        observations: List["Observation"],
        round_summary: str = "",
        replace_accumulated: bool = False,
    ) -> None:
        """
        保存一批 Observations

        Args:
          observations: 本轮新增的 Observation 列表
          round_summary: 本轮的总结文字(可选)
          replace_accumulated:
              True=清空累积重来(极端情况:用户明确说"重新开始")
              False=追加累积(默认,支持多实体并存)

        注意:
          通用实现**不做"换实体就覆盖"**的决定,因为这涉及领域语义,
          该决定应该由上层(app)根据 EntityScheme 语义来做。
          这里只提供数据存取,不掺入业务判断。
        """
        self.last_round_observations = list(observations)

        if replace_accumulated:
            self.accumulated_observations = list(observations)
        else:
            self.accumulated_observations.extend(observations)

        if round_summary:
            self.metadata.setdefault("rounds", []).append({
                "ts": time.time(),
                "summary": round_summary,
                "count": len(observations),
            })

    def reset_observations(self) -> None:
        """清空累积 observations(用户明确说"重新开始"时)"""
        self.accumulated_observations = []
        self.last_round_observations = []

    def get_all_observations(self) -> List["Observation"]:
        """取累积的全部 observations(上层用于投影)"""
        return list(self.accumulated_observations)

    def has_observations(self) -> bool:
        return bool(self.accumulated_observations)

    # -----------------------------------------------------------------
    # 上下文字段(轻量 KV)
    # -----------------------------------------------------------------
    def get_context(self, key: str, default: Any = None) -> Any:
        return self.context.get(key, default)

    def set_context(self, key: str, value: Any) -> None:
        self.context[key] = value

    def update_context(self, **kwargs) -> None:
        self.context.update(kwargs)

    # -----------------------------------------------------------------
    # 元信息
    # -----------------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "messages_count": len(self.messages),
            "observations_count": len(self.accumulated_observations),
            "context_keys": list(self.context.keys()),
        }


# ══════════════════════════════════════════════════════════════
# SessionStore - 多会话 LRU 管理
# ══════════════════════════════════════════════════════════════
class SessionStore:
    """
    会话存储 - 支持 LRU 淘汰 + TTL 过期

    默认用内存存储;生产环境可以继承这个类,重写 get/save/delete 对接 Redis
    """

    def __init__(self, max_sessions: int = 200, ttl_seconds: int = 3600):
        self._store: "OrderedDict[str, Session]" = OrderedDict()
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds

    def get_or_create(self, session_id: Optional[str] = None) -> Session:
        """按 id 取,取不到则新建"""
        self._cleanup_expired()
        if session_id and session_id in self._store:
            # LRU: 移到末尾
            self._store.move_to_end(session_id)
            return self._store[session_id]
        # 创建新会话
        session = Session(session_id=session_id)
        self._store[session.session_id] = session
        self._evict_if_full()
        return session

    def get(self, session_id: str) -> Optional[Session]:
        self._cleanup_expired()
        if session_id in self._store:
            self._store.move_to_end(session_id)
            return self._store[session_id]
        return None

    def delete(self, session_id: str) -> bool:
        if session_id in self._store:
            del self._store[session_id]
            return True
        return False

    def _cleanup_expired(self):
        now = time.time()
        expired = [
            sid for sid, s in self._store.items()
            if now - s.last_active > self.ttl_seconds
        ]
        for sid in expired:
            del self._store[sid]

    def _evict_if_full(self):
        """超过 max_sessions 时 LRU 淘汰最老的"""
        while len(self._store) > self.max_sessions:
            self._store.popitem(last=False)

    def size(self) -> int:
        return len(self._store)

    def all_session_ids(self) -> List[str]:
        self._cleanup_expired()
        return list(self._store.keys())
