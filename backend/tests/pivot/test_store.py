"""Pivot SQLite store: idempotent events, ticket lifecycle, runs/audit, work dir."""

import stat
from pathlib import Path

import pytest

from pivot.errors import EventNotFoundError, TicketNotFoundError
from pivot.model import TicketState
from pivot.store import PivotStore


@pytest.fixture
def store(tmp_path: Path):
    instance = PivotStore(tmp_path / "store.db", data_dir=tmp_path / "data")
    yield instance
    instance.close()


def add_event(store: PivotStore, *, key: str = "k1", event_type: str = "demo"):
    return store.add_event(
        source="manual",
        source_key=key,
        event_type=event_type,
        subject="A subject",
        summary="A summary",
        detail="A detail",
        link="https://example.com",
    )


def create_ticket(store: PivotStore, event_id: str, **overrides):
    values = {
        "event_id": event_id,
        "rule": "demo-rule",
        "title": "Ticket title",
        "cwd": "/tmp/project",
        "agent": "claude",
    }
    values.update(overrides)
    return store.create_ticket(**values)


def test_add_event_is_idempotent(store: PivotStore) -> None:
    first, created = add_event(store)
    again, created_again = add_event(store)

    assert created is True
    assert created_again is False
    assert again.id == first.id
    assert again.subject == "A subject"
    assert again.event_type == "demo"


def test_same_key_different_source_is_a_new_event(store: PivotStore) -> None:
    first, _ = store.add_event(source="manual", source_key="k1", event_type="a")
    second, created = store.add_event(source="webhook", source_key="k1", event_type="a")

    assert created is True
    assert second.id != first.id


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"source": "", "source_key": "k", "event_type": "a"}, "source"),
        ({"source": "manual", "source_key": "", "event_type": "a"}, "source_key"),
        ({"source": "manual", "source_key": "k", "event_type": " "}, "event_type"),
        (
            {"source": "manual", "source_key": "k", "event_type": "a", "hops": -1},
            "hops",
        ),
    ],
)
def test_add_event_validates(store: PivotStore, kwargs: dict, needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        store.add_event(**kwargs)


def test_create_and_get_ticket(store: PivotStore) -> None:
    event, _ = add_event(store)

    ticket = create_ticket(store, event.id)
    fetched = store.get_ticket(ticket.id)

    assert ticket.id.startswith("tkt-")
    assert ticket.state == TicketState.QUEUED.value
    assert ticket.agent_instance_id is None
    assert ticket.needs_human_since is None
    assert fetched == ticket


def test_create_ticket_requires_known_event(store: PivotStore) -> None:
    with pytest.raises(EventNotFoundError, match="evt-missing"):
        create_ticket(store, "evt-missing")


def test_update_ticket_lifecycle(store: PivotStore) -> None:
    event, _ = add_event(store)
    ticket = create_ticket(store, event.id)

    running = store.update_ticket(
        ticket.id,
        state=TicketState.RUNNING,
        agent_instance_id="inst-1",
        worktree_path="/tmp/worktree",
    )
    assert running.state == "running"
    assert running.agent_instance_id == "inst-1"
    assert running.worktree_path == "/tmp/worktree"
    assert running.updated_at >= ticket.updated_at

    waiting = store.update_ticket(
        ticket.id,
        state=TicketState.NEEDS_HUMAN,
        needs_human_since="2026-11-17T00:00:00Z",
    )
    assert waiting.needs_human_since == "2026-11-17T00:00:00Z"

    cleared = store.update_ticket(ticket.id, needs_human_since=None)
    assert cleared.needs_human_since is None
    assert cleared.state == TicketState.NEEDS_HUMAN.value


def test_update_unknown_ticket(store: PivotStore) -> None:
    with pytest.raises(TicketNotFoundError, match="tkt-missing"):
        store.update_ticket("tkt-missing", state=TicketState.RUNNING)


def test_update_rejects_unknown_state(store: PivotStore) -> None:
    event, _ = add_event(store)
    ticket = create_ticket(store, event.id)

    with pytest.raises(ValueError, match="state"):
        store.update_ticket(ticket.id, state="wandering")


def test_list_tickets_filters_orders_and_limits(store: PivotStore) -> None:
    event, _ = add_event(store)
    first = create_ticket(store, event.id, title="first")
    second = create_ticket(store, event.id, title="second")
    third = create_ticket(store, event.id, title="third")
    store.update_ticket(second.id, state=TicketState.RUNNING)

    all_tickets = store.list_tickets()
    limited = store.list_tickets(limit=2)
    running = store.list_tickets(state=TicketState.RUNNING)

    assert [ticket.id for ticket in all_tickets] == [third.id, second.id, first.id]
    assert [ticket.id for ticket in limited] == [third.id, second.id]
    assert [ticket.id for ticket in running] == [second.id]


@pytest.mark.parametrize("limit", [0, -1])
def test_list_tickets_rejects_bad_limit(store: PivotStore, limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        store.list_tickets(limit=limit)


def test_runs_and_audit(store: PivotStore) -> None:
    event, _ = add_event(store)
    ticket = create_ticket(store, event.id)

    run = store.add_run(ticket.id, status="spawning", attempt=1)
    assert run.id.startswith("run-")
    assert run.status == "spawning"

    finished = store.finish_run(run.id, status="failed", error="boom")
    assert finished.status == "failed"
    assert finished.error == "boom"
    assert finished.finished_at is not None

    store.audit(
        actor="dashboard", action="ticket.created", ticket_id=ticket.id, detail="x"
    )
    store.audit(actor="engine", action="ticket.dispatched")

    ticket_audit = store.list_audit(ticket.id)
    all_audit = store.list_audit()

    assert [entry.action for entry in ticket_audit] == ["ticket.created"]
    assert [entry.action for entry in all_audit] == [
        "ticket.dispatched",
        "ticket.created",
    ]


def test_finish_unknown_run(store: PivotStore) -> None:
    with pytest.raises(ValueError, match="run-unknown"):
        store.finish_run("run-unknown", status="done")


def test_delete_ticket_removes_records(store: PivotStore) -> None:
    event, _ = add_event(store)
    ticket = create_ticket(store, event.id)
    store.add_run(ticket.id, status="running")
    store.audit(actor="dashboard", action="ticket.created", ticket_id=ticket.id)

    assert store.delete_ticket(ticket.id) is True
    assert store.delete_ticket(ticket.id) is False
    assert store.get_ticket(ticket.id) is None
    assert store.list_runs(ticket.id) == []
    assert store.list_audit(ticket.id) == []
    assert store.get_event(event.id) is None


def test_write_input_creates_private_files(store: PivotStore, tmp_path: Path) -> None:
    event, _ = add_event(store)
    ticket = create_ticket(store, event.id)

    path = store.write_input(ticket.id, "# Task\n\ndo it")

    assert path == tmp_path / "data" / "work" / ticket.id / "input.md"
    assert path.read_text(encoding="utf-8") == "# Task\n\ndo it"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_remove_work_dir(store: PivotStore, tmp_path: Path) -> None:
    event, _ = add_event(store)
    ticket = create_ticket(store, event.id)
    path = store.write_input(ticket.id, "x")

    store.remove_work_dir(ticket.id)

    assert not path.parent.exists()
    assert (tmp_path / "data" / "work").is_dir()


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", ""])
def test_work_dir_rejects_unsafe_ids(store: PivotStore, bad: str) -> None:
    with pytest.raises(ValueError, match="ticket id"):
        store.work_dir(bad)


def test_store_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    first = PivotStore(path, data_dir=tmp_path / "data")
    event, _ = add_event(first)
    ticket = create_ticket(first, event.id)
    first.close()

    second = PivotStore(path, data_dir=tmp_path / "data")
    try:
        assert second.get_ticket(ticket.id) == ticket
        assert second.get_event(event.id) is not None
    finally:
        second.close()


def test_cron_state_roundtrip(store: PivotStore) -> None:
    assert store.get_cron_state("nightly") is None

    store.set_cron_state("nightly", "2026-11-16T09:00:00Z")

    assert store.get_cron_state("nightly") == "2026-11-16T09:00:00Z"

    store.set_cron_state("nightly", "2026-11-17T09:00:00Z")

    assert store.get_cron_state("nightly") == "2026-11-17T09:00:00Z"
    assert store.get_cron_state("other") is None


def test_cron_state_validates(store: PivotStore) -> None:
    with pytest.raises(ValueError, match="rule"):
        store.get_cron_state("")
    with pytest.raises(ValueError, match="last_fired_at"):
        store.set_cron_state("nightly", "")


def test_memory_store() -> None:
    instance = PivotStore(":memory:")
    try:
        event, created = add_event(instance)
        assert created is True
        assert instance.get_event(event.id) == event
    finally:
        instance.close()
