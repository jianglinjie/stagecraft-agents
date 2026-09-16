import asyncio

from stagecraft.api.events import Event, EventBus


async def take(subscription, n: int, timeout: float = 2.0) -> list[Event]:
    out: list[Event] = []

    async def collect() -> None:
        async for event in subscription:
            if event is None:
                continue
            out.append(event)
            if len(out) == n:
                return

    await asyncio.wait_for(collect(), timeout)
    return out


async def test_seq_is_per_session_and_strictly_increasing() -> None:
    bus = EventBus()
    assert [bus.publish("a", "x", {}).seq for _ in range(3)] == [1, 2, 3]
    assert bus.publish("b", "x", {}).seq == 1
    assert bus.last_seq("a") == 3


async def test_subscriber_gets_the_tail_then_live_events() -> None:
    bus = EventBus(replay_limit=2)
    for i in range(5):
        bus.publish("s", "item_delta", {"i": i})

    subscription = bus.subscribe("s")
    first_two = await take(subscription, 2)
    assert [e.seq for e in first_two] == [4, 5]

    bus.publish("s", "item_delta", {"i": 5})
    (live,) = await take(subscription, 1)
    assert live.seq == 6
    await subscription.aclose()
    assert bus.subscriber_count("s") == 0


async def test_last_event_id_replays_only_what_the_client_missed() -> None:
    bus = EventBus(replay_limit=1)
    for i in range(6):
        bus.publish("s", "e", {"i": i})

    events = await take(bus.subscribe("s", after_seq=3), 3)
    assert [e.seq for e in events] == [4, 5, 6]


async def test_an_event_published_during_replay_arrives_once_and_in_order() -> None:
    class RacyBus(EventBus):
        async def read_log(self, session_id, *, after_seq, limit):  # type: ignore[no-untyped-def]
            # Lands in the log AND in the already-registered queue: must not arrive twice.
            self.publish(session_id, "e", {"when": "before snapshot"})
            snapshot = await super().read_log(session_id, after_seq=after_seq, limit=limit)
            # Lands only in the queue: must not be lost.
            self.publish(session_id, "e", {"when": "after snapshot"})
            return snapshot

    bus = RacyBus()
    bus.publish("s", "e", {"when": "old"})
    bus.publish("s", "e", {"when": "old"})

    events = await take(bus.subscribe("s"), 4)
    assert [e.seq for e in events] == [1, 2, 3, 4]
    assert [e.data["when"] for e in events] == ["old", "old", "before snapshot", "after snapshot"]


async def test_a_client_further_behind_than_the_log_is_told_to_resync() -> None:
    bus = EventBus(log_size=3)
    for i in range(10):
        bus.publish("s", "e", {"i": i})

    events = await take(bus.subscribe("s", after_seq=2), 4)
    assert events[0].type == "resync"
    assert events[0].data["from_seq"] == 8
    assert [e.seq for e in events[1:]] == [8, 9, 10]


async def test_idle_subscriptions_heartbeat() -> None:
    bus = EventBus()
    subscription = bus.subscribe("s", heartbeat=0.01)
    assert await asyncio.wait_for(anext(subscription), 1) is None
    await subscription.aclose()


async def test_publishing_from_another_thread_is_delivered_in_order() -> None:
    bus = EventBus()
    subscription = bus.subscribe("s")
    first = asyncio.ensure_future(take(subscription, 50))
    await asyncio.sleep(0.01)

    await asyncio.to_thread(lambda: [bus.publish("s", "e", {"i": i}) for i in range(50)])
    events = await first
    assert [e.seq for e in events] == list(range(1, 51))


def test_sse_frame_format() -> None:
    event = Event(session_id="s", seq=7, type="state", data={"running": True}, created_at="t")
    assert event.to_sse() == (
        'id: 7\nevent: state\ndata: {"seq": 7, "created_at": "t", "running": true}\n\n'
    )
