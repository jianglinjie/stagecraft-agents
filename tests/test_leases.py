from pathlib import Path

import pytest

from stagecraft.api.leases import LeaseHeld, LeaseStore


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_one_live_holder_per_session() -> None:
    clock = Clock()
    leases = LeaseStore(clock=clock)
    lease = leases.acquire("s1", ttl=10)
    assert leases.holder("s1") == lease

    with pytest.raises(LeaseHeld):
        leases.acquire("s1", ttl=10)
    leases.acquire("s2", ttl=10)


def test_refresh_extends_and_needs_the_token() -> None:
    clock = Clock()
    leases = LeaseStore(clock=clock)
    lease = leases.acquire("s1", ttl=10)

    clock.now += 8
    renewed = leases.refresh(lease, ttl=10)
    assert renewed is not None and renewed.expires_at == clock.now + 10

    clock.now += 8
    with pytest.raises(LeaseHeld):
        leases.acquire("s1", ttl=10)

    impostor = type(lease)(session_id="s1", token="not-mine", expires_at=0)
    assert leases.refresh(impostor, ttl=10) is None
    assert leases.release(impostor) is False
    assert leases.holder("s1") is not None


def test_a_dead_holder_lease_runs_out_and_cannot_hurt_the_next_holder(tmp_path: Path) -> None:
    clock = Clock()
    db = tmp_path / "app.db"
    dead = LeaseStore(db, clock=clock).acquire("s1", ttl=10)
    # The process is gone: nobody refreshes, nobody releases.

    clock.now += 9.9
    with pytest.raises(LeaseHeld):
        LeaseStore(db, clock=clock).acquire("s1", ttl=10)

    clock.now += 0.2
    survivor_store = LeaseStore(db, clock=clock)
    assert survivor_store.holder("s1") is None
    new = survivor_store.acquire("s1", ttl=10)

    # The old process wakes up and tries to carry on.
    assert survivor_store.refresh(dead, ttl=10) is None
    assert survivor_store.release(dead) is False
    assert survivor_store.holder("s1") == new


def test_release_frees_the_session_immediately() -> None:
    leases = LeaseStore(clock=Clock())
    lease = leases.acquire("s1", ttl=60)
    assert leases.release(lease) is True
    assert leases.holder("s1") is None
    leases.acquire("s1", ttl=60)
