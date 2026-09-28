import asyncio
from datetime import timedelta

import pytest

from conftest import FakeSite, make_engine, wait_for, wait_idle
from stockwatch.core.engine import AlreadyActive, NotCancellable
from stockwatch.models import RunRecord, RunStatus, Trigger, utcnow
from stockwatch.storage import MemoryStore


def running(engine):
    return [j["category_id"] for j in engine.snapshot()["running"]]


def queued(engine):
    return [(j["category_id"], j["waiting"]) for j in engine.snapshot()["queued"]]


async def test_categories_of_one_site_never_run_in_parallel(tracker):
    site = FakeSite("shop", ("a", "b", "c", "d"), tracker=tracker)
    engine = make_engine(site)
    await engine.start()
    for key in "abcd":
        await engine.enqueue(f"shop:{key}")
    await wait_idle(engine)
    assert tracker.site_peak["shop"] == 1
    runs = await engine.store.list_runs()
    assert [r.status for r in runs] == [RunStatus.SUCCESS] * 4


async def test_global_limit_is_never_exceeded(tracker):
    sites = [FakeSite(f"s{i}", tracker=tracker) for i in range(6)]
    engine = make_engine(*sites)
    await engine.start()
    await engine.update_settings(max_concurrent_runs=2)
    for site in sites:
        await engine.enqueue(f"{site.key}:a")
    await wait_idle(engine)
    assert tracker.peak == 2


async def test_default_limit_is_five(tracker):
    sites = [FakeSite(f"s{i}", tracker=tracker) for i in range(7)]
    for s in sites:
        s.gate_all()
    engine = make_engine(*sites)
    await engine.start()
    for s in sites:
        await engine.enqueue(f"{s.key}:a")
    assert len(running(engine)) == 5
    assert [w for _, w in queued(engine)] == ["concurrency limit"] * 2
    for s in sites:
        s.release("a")
    await wait_idle(engine)


async def test_limit_changes_apply_immediately_without_killing_runs(tracker):
    sites = [FakeSite(f"s{i}", tracker=tracker) for i in range(6)]
    for s in sites:
        s.gate_all()
    engine = make_engine(*sites)
    await engine.start()
    for s in sites:
        await engine.enqueue(f"{s.key}:a")
    assert len(running(engine)) == 5 and len(queued(engine)) == 1

    await engine.update_settings(max_concurrent_runs=2)
    assert len(running(engine)) == 5  # lowering never cancels active runs

    for s in sites[:3]:
        s.release("a")
    await wait_for(lambda: len(running(engine)) == 2)
    assert len(queued(engine)) == 1  # still at the new cap of 2

    await engine.update_settings(max_concurrent_runs=6)
    assert len(running(engine)) == 3 and not queued(engine)
    for s in sites[3:]:
        s.release("a")
    await wait_idle(engine)
    assert engine.settings.max_concurrent_runs == 6
    assert (await engine.store.load_settings(engine.settings)).max_concurrent_runs == 6


async def test_busy_site_does_not_block_other_sites(tracker):
    a = FakeSite("a", ("x", "y"), tracker=tracker)
    b = FakeSite("b", ("x",), tracker=tracker)
    a.gate_all()
    b.gate_all()
    engine = make_engine(a, b)
    await engine.start()
    await engine.enqueue("a:x")
    await engine.enqueue("a:y")
    await engine.enqueue("b:x")
    assert sorted(running(engine)) == ["a:x", "b:x"]
    assert queued(engine) == [("a:y", "site busy")]

    a.release("x")
    await wait_for(lambda: running(engine) == ["b:x", "a:y"] or sorted(running(engine)) == ["a:y", "b:x"])
    a.release("y")
    b.release("x")
    await wait_idle(engine)


async def test_duplicate_enqueue_is_rejected():
    site = FakeSite("shop")
    site.gate_all()
    engine = make_engine(site)
    await engine.start()
    await engine.enqueue("shop:a")
    with pytest.raises(AlreadyActive):
        await engine.enqueue("shop:a")
    with pytest.raises(KeyError):
        await engine.enqueue("shop:nope")
    site.release("a")
    await wait_idle(engine)


async def test_manual_runs_jump_ahead_of_scheduled_ones():
    site = FakeSite("shop", ("a", "b", "c", "d"))
    site.gate_all()
    engine = make_engine(site)
    await engine.start()
    await engine.enqueue("shop:a", Trigger.SCHEDULE)
    await engine.enqueue("shop:b", Trigger.SCHEDULE)
    await engine.enqueue("shop:c", Trigger.MANUAL)
    await engine.enqueue("shop:d", Trigger.MANUAL)
    assert running(engine) == ["shop:a"]
    assert [c for c, _ in queued(engine)] == ["shop:c", "shop:d", "shop:b"]
    for key in "abcd":
        site.release(key)
    await wait_idle(engine)


async def test_scheduler_enqueues_due_categories_and_reschedules():
    site = FakeSite("shop", ("a", "b"))
    engine = make_engine(site)
    await engine.start()
    await engine.update_category("shop:b", enabled=False)

    before = utcnow()
    await engine.enqueue_due()
    await wait_idle(engine)
    cat_a = await engine.store.get_category("shop:a")
    cat_b = await engine.store.get_category("shop:b")
    assert cat_a.last_status == RunStatus.SUCCESS and cat_a.last_items == 1
    assert cat_a.next_run_at - cat_a.last_run_at == timedelta(minutes=10)  # jitter disabled
    assert cat_a.last_run_at >= before
    assert cat_b.last_run_at is None  # disabled

    await engine.enqueue_due()  # nothing is due any more
    assert not queued(engine) and not running(engine)


async def test_pause_stops_scheduled_runs_but_not_manual_ones():
    site = FakeSite("shop", ("a", "b"))
    engine = make_engine(site)
    await engine.start()
    await engine.update_settings(paused=True)
    await engine.enqueue_due()
    assert not queued(engine)

    await engine.enqueue("shop:a", Trigger.MANUAL)
    await wait_idle(engine)
    assert (await engine.store.get_category("shop:a")).last_status == RunStatus.SUCCESS

    await engine.update_settings(paused=False)
    await engine.enqueue_due()
    await wait_idle(engine)
    assert (await engine.store.get_category("shop:b")).last_status == RunStatus.SUCCESS


async def test_cancel_queued_and_running_runs():
    site = FakeSite("shop", ("a", "b"))
    site.gate_all()
    engine = make_engine(site)
    await engine.start()
    first = await engine.enqueue("shop:a")
    second = await engine.enqueue("shop:b", Trigger.SCHEDULE)

    await engine.cancel(second.run_id)
    assert not queued(engine)
    assert (await engine.store.get_category("shop:b")).next_run_at > utcnow()  # not re-queued next tick

    await engine.cancel(first.run_id)
    await wait_idle(engine)
    runs = {r.id: r for r in await engine.store.list_runs()}
    assert runs[first.run_id].status == RunStatus.CANCELLED
    assert runs[second.run_id].status == RunStatus.CANCELLED
    assert engine.store.products == {}  # cancelled scrape wrote nothing

    with pytest.raises(KeyError):
        await engine.cancel(first.run_id)


async def test_cannot_cancel_while_saving():
    site = FakeSite("shop")
    engine = make_engine(site)
    await engine.start()
    job = await engine.enqueue("shop:a")
    job.progress.phase = "saving"
    with pytest.raises(NotCancellable):
        await engine.cancel(job.run_id)
    await wait_idle(engine)


async def test_failed_scrape_is_recorded():
    site = FakeSite("shop")
    site.errors["https://shop.test/a"] = "HTTP 403 for page 1"
    engine = make_engine(site)
    await engine.start()
    await engine.enqueue("shop:a")
    await wait_idle(engine)
    run = (await engine.store.list_runs())[0]
    assert run.status == RunStatus.FAILED and run.error == "HTTP 403 for page 1"
    cat = await engine.store.get_category("shop:a")
    assert cat.last_status == RunStatus.FAILED and cat.last_error == "HTTP 403 for page 1"
    assert cat.next_run_at > utcnow()


async def test_run_timeout():
    site = FakeSite("shop")
    site.gate_all()  # never released
    engine = make_engine(site, run_timeout=0.05)
    await engine.start()
    await engine.enqueue("shop:a")
    await wait_idle(engine)
    run = (await engine.store.list_runs())[0]
    assert run.status == RunStatus.FAILED and "Timed out" in run.error


async def test_startup_interrupts_stale_runs_and_keeps_user_edits():
    store = MemoryStore()
    engine = make_engine(FakeSite("shop"), store=store)
    await engine.start()
    await engine.update_category("shop:a", interval_minutes=42, enabled=False)
    await store.insert_run(
        RunRecord(
            id="stale",
            category_id="shop:a",
            site="shop",
            category="a",
            trigger=Trigger.SCHEDULE,
            status=RunStatus.RUNNING,
            queued_at=utcnow(),
        )
    )
    await engine.stop()

    restarted = make_engine(FakeSite("shop"), store=store)
    await restarted.start()
    assert store.runs["stale"].status == RunStatus.INTERRUPTED
    cat = await store.get_category("shop:a")
    assert (cat.interval_minutes, cat.enabled) == (42, False)


async def test_stop_interrupts_active_runs():
    site = FakeSite("shop", ("a", "b"))
    site.gate_all()
    engine = make_engine(site)
    await engine.start()
    a = await engine.enqueue("shop:a")
    b = await engine.enqueue("shop:b")
    await asyncio.sleep(0)
    await engine.stop()
    assert engine.store.runs[a.run_id].status == RunStatus.INTERRUPTED
    assert engine.store.runs[b.run_id].status == RunStatus.INTERRUPTED


async def test_interval_edit_reschedules_from_last_run():
    engine = make_engine(FakeSite("shop"))
    await engine.start()
    await engine.enqueue("shop:a")
    await wait_idle(engine)
    cat = await engine.update_category("shop:a", interval_minutes=60)
    assert cat.next_run_at == cat.last_run_at + timedelta(minutes=60)


async def test_stats_are_cached_until_a_run_is_recorded():
    store = MemoryStore()
    calls = 0
    product_counts = store.product_counts

    async def counting_product_counts():
        nonlocal calls
        calls += 1
        return await product_counts()

    store.product_counts = counting_product_counts
    engine = make_engine(FakeSite("shop"), store=store)
    await engine.start()
    assert (await engine.stats())["products"] == {"total": 0, "in_stock": 0}
    await engine.stats()
    assert calls == 1

    await engine.enqueue("shop:a")
    await wait_idle(engine)
    stats = await engine.stats()
    assert stats["products"] == {"total": 1, "in_stock": 1}
    assert stats["runs_24h"] == {RunStatus.SUCCESS: 1}
    assert [r.status for r in stats["recent_runs"]] == [RunStatus.SUCCESS]
    assert calls == 2


async def test_ticker_sleeps_until_due_and_wakes_on_changes():
    engine = make_engine(FakeSite("shop"), tick_seconds=60)
    await engine.start()
    try:
        await wait_for(lambda: len(engine.store.runs) == 1)  # never run before, so due at once
        await wait_idle(engine)

        # Due in 0.1s: an edit wakes the ticker, which then sleeps only until then (not the 60s cap).
        await engine.store.update_category("shop:a", {"next_run_at": utcnow() + timedelta(seconds=0.1)})
        await engine.update_category("shop:a", enabled=True)
        await wait_for(lambda: len(engine.store.runs) == 2, timeout=2)
        await wait_idle(engine)

        await engine.update_settings(paused=True)
        await engine.store.update_category("shop:a", {"next_run_at": utcnow()})
        await asyncio.sleep(0.1)
        assert len(engine.store.runs) == 2
        await engine.update_settings(paused=False)
        await wait_for(lambda: len(engine.store.runs) == 3, timeout=2)
        await wait_idle(engine)
    finally:
        await engine.stop()
