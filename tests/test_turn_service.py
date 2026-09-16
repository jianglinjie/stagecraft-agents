"""TurnService without HTTP: what happens when the lease is taken away mid-turn."""

import asyncio
import threading

from stagecraft.agents.fake_model import FakeModel, reply, wait_for
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.api.app import build_services


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_a_turn_whose_lease_is_taken_over_stops_and_leaves_the_new_holder_alone() -> None:
    release = threading.Event()
    clock = Clock()
    models = {role: FakeModel([]) for role in ROLE_TOOLS}
    models["orchestrator"] = FakeModel([[wait_for(release, timeout=5), reply("too late")]])
    services = build_services(
        models_for=lambda _: models,  # type: ignore[return-value, arg-type]
        lease_ttl=10,
        refresh_every=0.02,
        clock=clock,
    )
    session = services.sessions.create_session()
    started = await services.turns.start_turn(session.id, "go")
    await asyncio.sleep(0.05)
    assert services.turns.is_running(session.id)

    # The process stalls past its TTL and another one takes the session over.
    clock.now += 11
    usurper = services.leases.acquire(session.id, ttl=60)

    await asyncio.sleep(0.1)
    release.set()
    await services.turns.wait_idle()

    (turn,) = services.sessions.turns(session.id)
    assert (turn.id, turn.status, turn.error) == (started.turn_id, "failed", "lease_lost")
    assert services.leases.holder(session.id) == usurper

    log = await services.bus.read_log(session.id, after_seq=0, limit=0)
    failed = [e for e in log if e.type == "turn_failed"]
    assert failed and failed[0].data["code"] == "lease_lost"
    assert not [e for e in log if e.type == "turn_completed"]


async def test_lease_is_refreshed_while_a_long_turn_runs() -> None:
    release = threading.Event()
    clock = Clock()
    models = {role: FakeModel([]) for role in ROLE_TOOLS}
    models["orchestrator"] = FakeModel([[wait_for(release, timeout=5), reply("done")]])
    services = build_services(
        models_for=lambda _: models,  # type: ignore[return-value, arg-type]
        lease_ttl=10,
        refresh_every=0.02,
        clock=clock,
    )
    session = services.sessions.create_session()
    await services.turns.start_turn(session.id, "go")

    for _ in range(5):  # 5 x 8s of clock time: far past one TTL
        await asyncio.sleep(0.05)
        clock.now += 8
    assert services.turns.is_running(session.id)

    release.set()
    await services.turns.wait_idle()
    assert services.sessions.turns(session.id)[0].status == "completed"
    assert not services.turns.is_running(session.id)
