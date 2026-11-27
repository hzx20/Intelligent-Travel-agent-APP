"""熔断器故障注入测试。

测试手法：注入假时钟（FakeClock），让「开路 → 等 30 秒冷却 → 半开」
的流程在毫秒级跑完，不需要真的 sleep。所有故障都是模拟的——
不打真实网络，断言的是状态机本身的转移逻辑。
"""
import threading

import pytest

from app.core import circuit_breaker as cb


class FakeClock:
    """可手动拨动的时钟：breaker 把它当 time.monotonic 用。"""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个测试前后都清空共享注册表，避免单例状态跨测试泄漏。"""
    cb.reset_all()
    yield
    cb.reset_all()


def test_closed_until_threshold_reached():
    """连续失败没到阈值（5 次）之前，必须一直是 closed 放行。"""
    clock = FakeClock()
    breaker = cb.CircuitBreaker("t1", clock=clock)
    for _ in range(4):  # 4 次失败：差一次，不该开路
        breaker.allow()
        breaker.record_failure()
    assert breaker.state() == cb.CLOSED
    assert breaker.allow() is True


def test_opens_after_consecutive_failures():
    """第 5 次连续失败触发开路，之后 allow() 直接拒绝。"""
    clock = FakeClock()
    breaker = cb.CircuitBreaker("t2", clock=clock)
    for _ in range(5):
        assert breaker.allow() is True
        breaker.record_failure()
    assert breaker.state() == cb.OPEN
    assert breaker.allow() is False  # 开路期间不再放行


def test_success_resets_consecutive_counter():
    """成功一次就清零连续失败计数：4 失败 + 1 成功 + 4 失败 = 仍然闭合。"""
    clock = FakeClock()
    breaker = cb.CircuitBreaker("t3", clock=clock)
    for _ in range(4):
        breaker.record_failure()
    breaker.record_success()  # 中间成功一次，计数归零
    for _ in range(4):
        breaker.record_failure()
    assert breaker.state() == cb.CLOSED  # 没到连续 5 次，不该开
    breaker.record_failure()  # 第 5 次连续失败
    assert breaker.state() == cb.OPEN


def test_rejected_calls_are_counted_and_cooldown_message_is_accurate():
    """开路期间的拒绝要计数，冷却剩余时间要能读出来。"""
    clock = FakeClock()
    breaker = cb.CircuitBreaker("t4", clock=clock)
    for _ in range(5):
        breaker.record_failure()
    clock.advance(12.0)
    assert breaker.cooldown_remaining() == pytest.approx(18.0)
    assert breaker.allow() is False
    assert breaker.snapshot()["rejected"] == 1


def test_half_open_grants_exactly_one_probe():
    """冷却结束后进入半开：第一个调用者拿到探测资格，其余被拒。"""
    clock = FakeClock()
    breaker = cb.CircuitBreaker("t5", clock=clock)
    for _ in range(5):
        breaker.record_failure()
    clock.advance(30.0)  # 冷却结束（惰性翻转：allow() 时才真正切状态）
    assert breaker.allow() is True       # 探测资格给了第一个
    assert breaker.allow() is False      # 第二个被拒（探测位占用中）
    assert breaker.state() == cb.HALF_OPEN


def test_probe_success_closes_breaker():
    """半开探测成功 → 回 closed，之后正常放行、连续失败计数清零。"""
    clock = FakeClock()
    breaker = cb.CircuitBreaker("t6", clock=clock)
    for _ in range(5):
        breaker.record_failure()
    clock.advance(30.0)
    assert breaker.allow() is True  # 探测
    breaker.record_success()
    assert breaker.state() == cb.CLOSED
    assert breaker.allow() is True  # 恢复正常放行


def test_probe_failure_reopens_and_requires_new_cooldown():
    """半开探测失败 → 重新开路，必须再等一整轮冷却才能探测。"""
    clock = FakeClock()
    breaker = cb.CircuitBreaker("t7", clock=clock)
    for _ in range(5):
        breaker.record_failure()
    clock.advance(30.0)
    assert breaker.allow() is True  # 第一次探测
    breaker.record_failure()        # 探测失败
    assert breaker.state() == cb.OPEN
    clock.advance(29.9)             # 还差 0.1 秒满冷却
    assert breaker.allow() is False
    clock.advance(0.1)              # 冷却刚好满了
    assert breaker.allow() is True  # 可以再探测


def test_concurrent_probe_only_one_wins():
    """并发场景：半开时 10 个线程同时抢探测资格，必须恰好 1 个成功。"""
    clock = FakeClock()
    breaker = cb.CircuitBreaker("t8", clock=clock)
    for _ in range(5):
        breaker.record_failure()
    clock.advance(30.0)

    granted = []
    barrier = threading.Barrier(10)

    def try_allow():
        barrier.wait()          # 10 个线程站到同一起跑线再放行，制造真实竞争
        granted.append(breaker.allow())

    threads = [threading.Thread(target=try_allow) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for ok in granted if ok) == 1


def test_circuit_open_error_carries_name_and_remaining():
    """CircuitOpen 异常要带上熔断器名字和剩余冷却时间，方便上层报错。"""
    clock = FakeClock()
    breaker = cb.CircuitBreaker("chat", clock=clock)
    for _ in range(5):
        breaker.record_failure()
    clock.advance(10.0)
    err = cb.CircuitOpen("chat", breaker.cooldown_remaining())
    assert err.breaker_name == "chat"
    assert err.cooldown_remaining == pytest.approx(20.0)
    assert "chat" in str(err)


def test_call_wrapper_records_success_and_failure():
    """call() 包装器：成功记成功，异常记失败并原样上抛（不吞异常）。"""
    clock = FakeClock()
    breaker = cb.CircuitBreaker("t10", clock=clock)

    assert breaker.call(lambda: 42) == 42
    assert breaker.snapshot()["successes"] == 1

    def boom():
        raise ValueError("provider down")

    with pytest.raises(ValueError):
        breaker.call(boom)
    assert breaker.snapshot()["failures"] == 1
    assert breaker.snapshot()["consecutive_failures"] == 1


def test_call_wrapper_raises_circuit_open_when_open():
    """开路期间 call() 直接抛 CircuitOpen，根本不执行目标函数。"""
    clock = FakeClock()
    breaker = cb.CircuitBreaker("t11", clock=clock)
    for _ in range(5):
        breaker.record_failure()

    executed = []

    def should_not_run():
        executed.append(1)
        return "ran"

    with pytest.raises(cb.CircuitOpen):
        breaker.call(should_not_run)
    assert executed == []  # 目标函数没被执行——这就是熔断省下的超时等待


def test_env_overrides_threshold_and_cooldown(monkeypatch):
    """环境变量可以在不传参的情况下调整阈值与冷却时间。"""
    monkeypatch.setenv("TRAVEL_BREAKER_THRESHOLD", "2")
    monkeypatch.setenv("TRAVEL_BREAKER_COOLDOWN", "5")
    clock = FakeClock()
    breaker = cb.CircuitBreaker("t12", clock=clock)
    assert breaker.failure_threshold == 2
    assert breaker.cooldown == pytest.approx(5.0)

    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state() == cb.OPEN      # 2 次就开路
    clock.advance(5.0)
    assert breaker.allow() is True          # 5 秒冷却后可探测


def test_invalid_env_falls_back_to_defaults(monkeypatch):
    """环境变量写错（非数字）时静默用默认值：配置错误不能带崩应用。"""
    monkeypatch.setenv("TRAVEL_BREAKER_THRESHOLD", "abc")
    monkeypatch.setenv("TRAVEL_BREAKER_COOLDOWN", "not-a-number")
    breaker = cb.CircuitBreaker("t13")
    assert breaker.failure_threshold == cb.DEFAULT_FAILURE_THRESHOLD
    assert breaker.cooldown == cb.DEFAULT_COOLDOWN


def test_registry_shares_instances_and_reset_all_clears():
    """同名共享同一实例；reset_all 清空注册表。"""
    a = cb.get_breaker("chat")
    b = cb.get_breaker("chat")
    assert a is b
    a.record_failure()
    assert cb.all_snapshots()[0]["failures"] == 1
    cb.reset_all()
    assert cb.all_snapshots() == []
    assert cb.get_breaker("chat") is not a  # 新实例，旧状态不复存在


def test_snapshot_shape_is_metrics_ready():
    """snapshot 的字段要齐且类型正确，/metrics 直接消费。"""
    breaker = cb.CircuitBreaker("t15")
    snap = breaker.snapshot()
    expected = {
        "name", "state", "consecutive_failures", "failure_threshold",
        "cooldown_seconds", "probe_in_flight", "cooldown_remaining",
        "accepted", "rejected", "successes", "failures", "opens",
    }
    assert expected == set(snap.keys())
    assert snap["state"] == cb.CLOSED
    assert isinstance(snap["accepted"], int)
    assert isinstance(snap["opens"], int)


def test_opens_counter_counts_each_trip():
    """每次真正开路（含半开探测失败后重新开路）opens 都要 +1。"""
    clock = FakeClock()
    breaker = cb.CircuitBreaker("t16", clock=clock)
    for _ in range(5):
        breaker.record_failure()
    assert breaker.snapshot()["opens"] == 1
    clock.advance(30.0)
    breaker.allow()
    breaker.record_failure()  # 半开探测失败 → 重新开路
    assert breaker.snapshot()["opens"] == 2
