"""三态熔断器：防止对持续故障的外部服务反复撞墙。

先分清两个容易混的概念（两者互补，不能互相替代）：
- 限流（provider_budget.py 做的事）：服务是好的，但容量有限。平时排队，
  大家都能过，只是慢一点，超了会抛 ProviderBusy。
- 熔断（本文件做的事）：服务已经挂了（或网络断了）。再排队也只是排队到
  一个永远不会应答的服务上，白白耗超时。正确的做法是「先别打，隔一会儿
  试一次，确认恢复了再恢复流量」。

三态模型（与 Netflix Hystrix / resilience4j 同一套语义）：
- closed（闭合 = 正常放行）：连续失败次数没到阈值，请求照常通过。
- open（打开 = 拒绝）：连续失败达到阈值，冷却时间内所有请求直接拒绝，
  不再消耗超时等待，也让故障服务有机会喘口气。
- half-open（半开 = 试探）：冷却时间到了，只放行 1 个探测请求去试真实服务。
  探测成功 → 回到 closed；探测失败 → 重新 open，再等一轮冷却。

为什么失败计数用「连续失败」而不是「时间窗口内的失败率」：
本项目的调用量是个人/课设级，一分钟可能就几个请求，滑动窗口在低流量下
没有统计意义。连续失败语义简单、行为可预测、测试好写，代价只是对
「间歇性抖动」不敏感——而间歇性抖动恰恰是重试机制该处理的事。

线程安全：状态翻转用一把锁保护。调用方可能是 asyncio 事件循环里的线程池
线程（检索）或直接的事件循环（LLM），锁只保护几个整数赋值，不会有竞争问题。

降级契约：本模块只负责「判断」和「计数」，不做任何 IO、不抛除
CircuitOpen 之外的异常。调用方（llm.py / rerank 等）拿到 CircuitOpen 后
走各自的降级路径。
"""
from __future__ import annotations

import os
import threading
import time

# 三态常量。字符串而非 enum：/metrics 与日志里直接可读，不用转译。
CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"

# 默认参数：连续 5 次失败开路，30 秒冷却。与交接文档第 4 节的约定一致。
DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_COOLDOWN = 30.0


class CircuitOpen(RuntimeError):
    """熔断打开（或半开探测位被占）期间的拒绝信号。

    继承 RuntimeError 是有意的：调用方现有的 except 兜底逻辑
    （比如 llm.py 里「网络、限流、返回结构异常都走同一套重试」）
    不用加新的 except 分支就能接住它。
    """

    def __init__(self, name: str, cooldown_remaining: float = 0.0):
        self.breaker_name = name
        self.cooldown_remaining = max(0.0, float(cooldown_remaining))
        if self.cooldown_remaining > 0:
            detail = f"约 {self.cooldown_remaining:.0f}s 后进入半开探测"
        else:
            detail = "探测请求正在被另一个调用方使用"
        super().__init__(f"熔断器[{name}]处于打开状态，{detail}")


class CircuitBreaker:
    """单个外部服务的熔断器实例。用 get_breaker() 取共享实例，一般不直接构造。"""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int | None = None,
        cooldown: float | None = None,
        clock=time.monotonic,
    ):
        """
        name               服务名（chat / rerank / embedding），只用于日志与 metrics。
        failure_threshold  连续失败多少次后开路。None 时读环境变量 TRAVEL_BREAKER_THRESHOLD，
                           再退回默认值 5。显式传参优先级最高，方便测试注入。
        cooldown           开路后等多久进入半开。None 时读 TRAVEL_BREAKER_COOLDOWN，默认 30s。
        clock              时间源，默认 time.monotonic。测试里注入假时钟避免真等 30 秒。
        """
        self.name = str(name)
        self.failure_threshold = self._int_param(
            failure_threshold, "TRAVEL_BREAKER_THRESHOLD", DEFAULT_FAILURE_THRESHOLD
        )
        self.cooldown = self._float_param(
            cooldown, "TRAVEL_BREAKER_COOLDOWN", DEFAULT_COOLDOWN
        )
        self._clock = clock
        self._lock = threading.Lock()

        # ---- 可变状态（全部在锁内读写）----
        self._state = CLOSED
        self._consecutive_failures = 0   # closed 态下的连续失败计数；成功一次清零
        self._opened_at = 0.0            # 最近一次开路的时刻，用来判断冷却是否结束
        self._probe_in_flight = False    # half-open 态下探测位是否已被占用

        # ---- 累计统计（只增不减，给 /metrics 用；也在锁内读写）----
        self._accepted = 0       # 放行次数（含探测）
        self._rejected = 0       # 被熔断直接拒绝的次数
        self._successes = 0      # 上报成功的次数
        self._failures = 0       # 上报失败的次数
        self._opens = 0          # 累计开路次数（衡量这个服务坏过几回）

    # ------------------------------------------------ 参数解析

    @staticmethod
    def _int_param(value, env_name: str, default: int) -> int:
        """显式传参 > 环境变量 > 默认值。环境变量非法时静默用默认值：
        熔断器的配置错了不该把整个应用带崩。"""
        if value is not None:
            return max(1, int(value))
        raw = os.environ.get(env_name, "")
        try:
            return max(1, int(raw)) if raw else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float_param(value, env_name: str, default: float) -> float:
        if value is not None:
            return max(0.1, float(value))
        raw = os.environ.get(env_name, "")
        try:
            return max(0.1, float(raw)) if raw else default
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------ 判定与上报

    def allow(self) -> bool:
        """询问是否放行本次调用。返回 False 时调用方应立刻走降级路径，
        不要再碰外部服务。

        half-open 的探测位只有 1 个：冷却结束后第一个调用 allow() 的人拿到
        探测资格，其余调用方在探测完成前一律拒绝——避免冷却刚过就被并发
        请求瞬间打回去。
        """
        with self._lock:
            now = self._clock()
            self._transition_locked(now)
            if self._state == CLOSED:
                self._accepted += 1
                return True
            if self._state == OPEN:
                self._rejected += 1
                return False
            # half_open：探测位空闲才放行
            if self._probe_in_flight:
                self._rejected += 1
                return False
            self._probe_in_flight = True
            self._accepted += 1
            return True

    def record_success(self) -> None:
        """调用成功后上报。半开探测成功 → 回 closed；closed 态下成功 → 连续失败清零。"""
        with self._lock:
            self._successes += 1
            if self._state == HALF_OPEN:
                self._probe_in_flight = False
                self._state = CLOSED
            self._consecutive_failures = 0

    def record_failure(self) -> None:
        """调用失败后上报。half-open 探测失败 → 立刻重新开路（不等阈值，
        因为探测的意义就是「确认服务还在不在」，不在就继续冷却）。"""
        with self._lock:
            now = self._clock()
            self._failures += 1
            if self._state == HALF_OPEN:
                self._probe_in_flight = False
                self._trip_locked(now)
                return
            # closed 态：累计连续失败，到阈值开路
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._trip_locked(now)

    def _transition_locked(self, now: float) -> None:
        """惰性状态翻转：open → half-open 不靠后台定时器，而是等下一次
        allow() 来问的时候顺便判断冷却是否结束。2G 服务器上少一个线程
        就少一份内存和调度开销。"""
        if self._state == OPEN and now - self._opened_at >= self.cooldown:
            self._state = HALF_OPEN

    def _trip_locked(self, now: float) -> None:
        """开路。opens 只在真正从非 open 态进入 open 时 +1，
        半开探测失败重新开路也算一次（服务又被确认坏了一回）。"""
        if self._state != OPEN:
            self._opens += 1
        self._state = OPEN
        self._opened_at = now
        self._consecutive_failures = 0

    # ------------------------------------------------ 只读视图

    def state(self) -> str:
        """当前三态。注意：open 冷却结束后、下次 allow() 之前，这里仍返回 open
        （惰性翻转的设计结果）。要看「即将进入半开」用 cooldown_remaining()。"""
        with self._lock:
            self._transition_locked(self._clock())
            return self._state

    def cooldown_remaining(self) -> float:
        """open 态下还剩多少秒进入半开；其他状态返回 0。给 CircuitOpen 的
        报错信息和 /metrics 用。"""
        with self._lock:
            now = self._clock()
            self._transition_locked(now)
            if self._state == OPEN:
                return max(0.0, self.cooldown - (now - self._opened_at))
            return 0.0

    def snapshot(self) -> dict:
        """给 /metrics 与排障用的只读快照。字段名与 Prometheus 习惯对齐，
        值全是标量，方便直接映射成 gauge。"""
        with self._lock:
            now = self._clock()
            self._transition_locked(now)
            if self._state == OPEN:
                remaining = max(0.0, self.cooldown - (now - self._opened_at))
            else:
                remaining = 0.0
            return {
                "name": self.name,
                "state": self._state,
                "consecutive_failures": self._consecutive_failures,
                "failure_threshold": self.failure_threshold,
                "cooldown_seconds": self.cooldown,
                "probe_in_flight": self._probe_in_flight,
                "cooldown_remaining": remaining,
                "accepted": self._accepted,
                "rejected": self._rejected,
                "successes": self._successes,
                "failures": self._failures,
                "opens": self._opens,
            }

    def call(self, func, *args, **kwargs):
        """标准调用包装：先问 allow()，被拒直接抛 CircuitOpen（目标函数不执行，
        这就是熔断省下的超时等待）；放行则执行并按结果上报成功/失败。
        目标函数抛出的原始异常原样上抛，本方法不做任何吞异常处理——
        降级是调用方的职责，熔断器只负责计数和判定。"""
        if not self.allow():
            raise CircuitOpen(self.name, self.cooldown_remaining())
        try:
            result = func(*args, **kwargs)
        except BaseException:
            self.record_failure()
            raise
        self.record_success()
        return result

    def reset(self) -> None:
        """回到出厂状态。只给测试用：断言之间要一个干净的熔断器。"""
        with self._lock:
            self._state = CLOSED
            self._consecutive_failures = 0
            self._opened_at = 0.0
            self._probe_in_flight = False


# ---------------------------------------------------------------- 共享注册表
#
# 全局按服务名共享实例：chat、rerank 等模块各自通过 get_breaker("xxx") 拿
# 同一个对象，状态才能跨请求累积。reset_all() 供测试隔离。

_BREAKERS: dict[str, CircuitBreaker] = {}
_REGISTRY_LOCK = threading.Lock()


def get_breaker(name: str, **kwargs) -> CircuitBreaker:
    """按名字取共享熔断器实例，没有则创建。同名不同参以先创建的为准。"""
    key = str(name)
    with _REGISTRY_LOCK:
        breaker = _BREAKERS.get(key)
        if breaker is None:
            breaker = CircuitBreaker(key, **kwargs)
            _BREAKERS[key] = breaker
        return breaker


def reset_all() -> None:
    """清空注册表。只给测试用（模块级单例会跨测试泄漏状态）。"""
    with _REGISTRY_LOCK:
        _BREAKERS.clear()


def all_snapshots() -> list[dict]:
    """所有熔断器的快照，/metrics 直接遍历它。"""
    with _REGISTRY_LOCK:
        breakers = list(_BREAKERS.values())
    return [b.snapshot() for b in breakers]
