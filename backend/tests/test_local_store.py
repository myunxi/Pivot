"""LocalStore CRUD + pending-delivery semantics (desktop local-only mode)."""

from pathlib import Path
from typing import Any

import pytest

from vicoa.local_server.store import (
    InstanceConflictError,
    InstanceNotFoundError,
    LocalStore,
)


@pytest.fixture
def store(tmp_path: Path):
    s = LocalStore(tmp_path / "local_store.db")
    yield s
    s.close()


def _register(store: LocalStore, **kwargs) -> str:
    defaults: dict[str, Any] = dict(agent_type="Claude Code", project="/tmp/proj")
    defaults.update(kwargs)
    return store.register_instance(**defaults).id


def _queue(message: Any) -> dict[str, Any]:
    assert message.message_metadata is not None
    return message.message_metadata["queue"]


# ----------------------------------------------------------------------
# Instances
# ----------------------------------------------------------------------
def test_register_and_get_instance(store: LocalStore) -> None:
    inst = store.register_instance(
        agent_type="Claude Code",
        agent_instance_id="11111111-1111-1111-1111-111111111111",
        name="my session",
        project="/tmp/proj",
        transport="ws",
        source="app",
        session_config={"agent": "claude"},
    )
    assert inst.id == "11111111-1111-1111-1111-111111111111"
    assert inst.status == "ACTIVE"
    assert inst.machine_id is None
    assert inst.instance_metadata == {"transport": "ws", "source": "app"}
    fetched = store.get_instance(inst.id)
    assert fetched is not None
    assert fetched.session_config == {"agent": "claude"}
    assert fetched.started_at.endswith("Z")


def test_register_duplicate_id_conflicts(store: LocalStore) -> None:
    instance_id = _register(store)
    with pytest.raises(InstanceConflictError):
        store.register_instance(agent_type="Claude Code", agent_instance_id=instance_id)


def test_update_status_and_terminal_semantics(store: LocalStore) -> None:
    instance_id = _register(store)
    updated = store.update_instance_status(instance_id, "AWAITING_INPUT")
    assert updated.status == "AWAITING_INPUT"
    completed = store.update_instance_status(instance_id, "COMPLETED")
    assert completed.ended_at is not None
    with pytest.raises(ValueError, match="Invalid status"):
        store.update_instance_status(instance_id, "NOT_A_STATUS")
    with pytest.raises(InstanceNotFoundError):
        store.update_instance_status("missing-id", "ACTIVE")


def test_patch_instance_merges_session_config(store: LocalStore) -> None:
    instance_id = _register(store, session_config={"agent": "claude", "model": "a"})
    patched = store.patch_instance(
        instance_id, name="renamed", session_config_merge={"model": "b"}
    )
    assert patched.name == "renamed"
    assert patched.session_config == {"agent": "claude", "model": "b"}


# ----------------------------------------------------------------------
# Messages + pending delivery
# ----------------------------------------------------------------------
def test_agent_message_collects_queued_user_messages(store: LocalStore) -> None:
    instance_id = _register(store)
    # Renderer-style user message: mark_as_read=False keeps it pending.
    store.add_user_message(
        agent_instance_id=instance_id, content="first", mark_as_read=False
    )
    message, instance, queued = store.add_agent_message(
        agent_instance_id=instance_id, content="reply", requires_user_input=False
    )
    assert [m.content for m in queued] == ["first"]
    assert instance.status == "ACTIVE"
    # The new agent message becomes the read cursor.
    assert instance.last_read_message_id == message.id


def test_agent_message_creates_instance_when_missing(store: LocalStore) -> None:
    message, instance, queued = store.add_agent_message(
        agent_instance_id="22222222-2222-2222-2222-222222222222",
        content="hello",
        agent_type="Claude Code",
        requires_user_input=True,
    )
    assert instance.id == "22222222-2222-2222-2222-222222222222"
    assert instance.status == "AWAITING_INPUT"
    assert queued == []
    with pytest.raises(ValueError, match="agent_type is required"):
        store.add_agent_message(agent_instance_id="unknown", content="x")


def test_pending_messages_deliver_once(store: LocalStore) -> None:
    instance_id = _register(store)
    store.add_user_message(
        agent_instance_id=instance_id, content="ping", mark_as_read=False
    )
    messages, status = store.pending_messages(instance_id, None)
    assert status == "ok"
    assert [m.content for m in messages] == ["ping"]
    # Delivery advanced the cursor: nothing pending on the next poll.
    again, status = store.pending_messages(instance_id, messages[-1].id)
    assert status == "ok"
    assert again == []


def test_pending_messages_stale_cursor(store: LocalStore) -> None:
    instance_id = _register(store)
    store.add_user_message(
        agent_instance_id=instance_id, content="ping", mark_as_read=False
    )
    store.pending_messages(instance_id, None)
    _, status = store.pending_messages(instance_id, "not-the-cursor")
    assert status == "stale"


def test_mark_as_read_user_message_is_not_pending(store: LocalStore) -> None:
    instance_id = _register(store)
    store.add_user_message(
        agent_instance_id=instance_id, content="already-read", mark_as_read=True
    )
    messages, status = store.pending_messages(instance_id, None)
    assert status == "ok"
    assert messages == []


def test_mark_message_requires_input(store: LocalStore) -> None:
    instance_id = _register(store)
    agent_msg, _, _ = store.add_agent_message(
        agent_instance_id=instance_id, content="question?"
    )
    returned_id, queued, status = store.mark_message_requires_input(agent_msg.id)
    assert returned_id == instance_id
    assert queued == []
    assert status == "ok"
    inst = store.get_instance(instance_id)
    assert inst is not None and inst.status == "AWAITING_INPUT"
    with pytest.raises(ValueError, match="already requires"):
        store.mark_message_requires_input(agent_msg.id)


# ----------------------------------------------------------------------
# WS catch-up fetches
# ----------------------------------------------------------------------
def test_fetch_messages_scope_policies(store: LocalStore) -> None:
    instance_id = _register(store)
    store.add_user_message(
        agent_instance_id=instance_id, content="u1", mark_as_read=False
    )
    store.add_agent_message(agent_instance_id=instance_id, content="a1")

    # User-scoped (renderer): full conversation.
    rows, has_more, resync = store.fetch_messages(
        instance_id, None, include_agent_messages=True
    )
    assert resync is None and has_more is False
    assert [r["content"] for r in rows] == ["u1", "a1"]
    assert rows[0]["t"] == "new-message"
    assert rows[0]["instance_id"] == instance_id

    # Session-scoped (wrapper): USER-only, and the last_read cursor (advanced
    # by the agent reply) suppresses already-processed rows.
    rows, _, _ = store.fetch_messages(instance_id, None, include_agent_messages=False)
    assert rows == []


def test_fetch_messages_pagination_cursor(store: LocalStore) -> None:
    instance_id = _register(store)
    for index in range(5):
        store.add_user_message(
            agent_instance_id=instance_id, content=f"m{index}", mark_as_read=False
        )
    first, has_more, _ = store.fetch_messages(
        instance_id, None, include_agent_messages=True, page_limit=2
    )
    assert [r["content"] for r in first] == ["m0", "m1"]
    assert has_more is True
    second, _, _ = store.fetch_messages(
        instance_id,
        {"created_at": first[-1]["created_at"], "id": first[-1]["id"]},
        include_agent_messages=True,
        page_limit=10,
    )
    assert [r["content"] for r in second] == ["m2", "m3", "m4"]


def test_fetch_messages_missing_cursor_requests_resync(store: LocalStore) -> None:
    instance_id = _register(store)
    rows, has_more, resync = store.fetch_messages(
        instance_id,
        {"created_at": "2026-01-01T00:00:00.000000Z", "id": "gone"},
        include_agent_messages=True,
    )
    assert rows == [] and has_more is False
    assert resync == "missing_history"


def test_fetch_instances_watermark(store: LocalStore) -> None:
    instance_id = _register(store)
    rows = store.fetch_instances(None)
    assert [r["id"] for r in rows] == [instance_id]
    assert rows[0]["t"] == "instance-update"
    # A watermark far in the future (beyond the safety window) filters it out.
    rows = store.fetch_instances("2999-01-01T00:00:00.000000Z")
    assert rows == []


# ----------------------------------------------------------------------
# Event hook
# ----------------------------------------------------------------------
def test_listener_receives_envelope_payloads(store: LocalStore) -> None:
    events: list[dict] = []
    store.add_listener(events.append)
    instance_id = _register(store)
    store.add_user_message(
        agent_instance_id=instance_id, content="hi", mark_as_read=False
    )
    kinds = [e["body"]["t"] for e in events]
    assert kinds == ["instance-created", "new-message"]
    assert events[0]["entity"] == "agent_instances"
    assert events[1]["entity"] == "messages"
    assert events[1]["body"]["content"] == "hi"


def test_recent_directories_roundtrip(store: LocalStore) -> None:
    store.push_recent_directory("/tmp/a")
    store.push_recent_directory("/tmp/b")
    store.push_recent_directory("/tmp/a")
    assert store.recent_directories() == ["/tmp/a", "/tmp/b"]


# ----------------------------------------------------------------------
# Heartbeat + queue lifecycle (direct-mode runner)
# ----------------------------------------------------------------------
def test_heartbeat_instance_updates_timestamp_and_emits(store: LocalStore) -> None:
    instance_id = _register(store)
    before = store.get_instance(instance_id)
    assert before is not None
    events: list[dict] = []
    store.add_listener(events.append)

    updated = store.heartbeat_instance(instance_id)

    assert updated.last_heartbeat_at is not None
    assert updated.last_heartbeat_at >= (before.last_heartbeat_at or "")
    assert events[-1]["entity"] == "agent_instances"
    assert events[-1]["body"]["t"] == "instance-update"
    with pytest.raises(InstanceNotFoundError):
        store.heartbeat_instance("missing-id")


def test_mark_message_consumed_stamps_queue_and_emits(store: LocalStore) -> None:
    instance_id = _register(store)
    message = store.add_user_message(
        agent_instance_id=instance_id,
        content="queued",
        mark_as_read=False,
        message_metadata={"queue": {"status": "queued"}},
    )
    events: list[dict] = []
    store.add_listener(events.append)

    updated = store.mark_message_consumed(message.id)

    queue = _queue(updated)
    assert queue["status"] == "consumed"
    assert isinstance(queue["consumed_at"], str)
    assert "steered" not in queue
    assert events[-1]["entity"] == "messages"
    assert events[-1]["body"]["t"] == "message-update"
    assert events[-1]["event_id"] == f"{message.id}:queue:consumed"
    with pytest.raises(InstanceNotFoundError):
        store.mark_message_consumed("missing-id")


def test_mark_message_consumed_records_steered(store: LocalStore) -> None:
    instance_id = _register(store)
    message = store.add_user_message(
        agent_instance_id=instance_id, content="steer me", mark_as_read=False
    )

    updated = store.mark_message_consumed(message.id, steered=True)

    assert _queue(updated)["steered"] is True


def test_mark_message_consumed_never_overwrites_cancelled(store: LocalStore) -> None:
    instance_id = _register(store)
    message = store.add_user_message(
        agent_instance_id=instance_id,
        content="cancelled",
        mark_as_read=False,
        message_metadata={"queue": {"status": "cancelled"}},
    )
    events: list[dict] = []
    store.add_listener(events.append)

    updated = store.mark_message_consumed(message.id)

    assert _queue(updated) == {"status": "cancelled"}
    assert events == []


def test_user_message_is_queued_while_instance_is_active(
    store: LocalStore,
) -> None:
    instance_id = _register(store)
    events: list[dict] = []
    store.add_listener(events.append)

    message = store.add_user_message(
        agent_instance_id=instance_id, content="while busy", mark_as_read=False
    )

    assert _queue(message)["status"] == "queued"
    assert events[-1]["body"]["message_metadata"]["queue"]["status"] == "queued"


def test_user_message_is_not_queued_when_awaiting_input(
    store: LocalStore,
) -> None:
    instance_id = _register(store)
    store.update_instance_status(instance_id, "AWAITING_INPUT")

    message = store.add_user_message(
        agent_instance_id=instance_id, content="next turn", mark_as_read=False
    )

    assert message.message_metadata is None


def test_user_message_preserves_an_explicit_queue_status(
    store: LocalStore,
) -> None:
    instance_id = _register(store)

    message = store.add_user_message(
        agent_instance_id=instance_id,
        content="already steering",
        mark_as_read=False,
        message_metadata={"queue": {"status": "steer"}},
    )

    assert _queue(message) == {"status": "steer"}


def test_mark_as_read_user_message_is_not_queued(store: LocalStore) -> None:
    instance_id = _register(store)

    message = store.add_user_message(
        agent_instance_id=instance_id, content="sdk write", mark_as_read=True
    )

    assert message.message_metadata is None


def test_cancel_queued_message(store: LocalStore) -> None:
    instance_id = _register(store)
    message = store.add_user_message(
        agent_instance_id=instance_id, content="cancel me", mark_as_read=False
    )
    events: list[dict] = []
    store.add_listener(events.append)

    assert store.cancel_queued_message(instance_id, message.id) is True

    updated = store.get_instance_messages(instance_id, message_limit=10)[0]
    queue = _queue(updated)
    assert queue["status"] == "cancelled"
    assert isinstance(queue["cancelled_at"], str)
    assert events[-1]["event_id"] == f"{message.id}:queue:cancelled"
    # Only a plainly queued message can be cancelled once.
    assert store.cancel_queued_message(instance_id, message.id) is False


def test_cancel_does_not_override_consumed(store: LocalStore) -> None:
    instance_id = _register(store)
    message = store.add_user_message(
        agent_instance_id=instance_id, content="taken", mark_as_read=False
    )
    store.mark_message_consumed(message.id)

    assert store.cancel_queued_message(instance_id, message.id) is False
    updated = store.get_instance_messages(instance_id, message_limit=10)[0]
    assert _queue(updated)["status"] == "consumed"


def test_steer_queued_message(store: LocalStore) -> None:
    instance_id = _register(store)
    message = store.add_user_message(
        agent_instance_id=instance_id, content="steer me", mark_as_read=False
    )
    events: list[dict] = []
    store.add_listener(events.append)

    assert store.steer_queued_message(instance_id, message.id) is True

    updated = store.get_instance_messages(instance_id, message_limit=10)[0]
    queue = _queue(updated)
    assert queue["status"] == "steer"
    assert isinstance(queue["steer_requested_at"], str)
    assert events[-1]["event_id"] == f"{message.id}:queue:steer"
    assert store.steer_queued_message(instance_id, message.id) is False


def test_cancel_and_steer_require_the_owning_instance(store: LocalStore) -> None:
    instance_id = _register(store)
    other_id = _register(store, agent_instance_id="other-instance")
    message = store.add_user_message(
        agent_instance_id=instance_id, content="mine", mark_as_read=False
    )

    with pytest.raises(InstanceNotFoundError):
        store.cancel_queued_message(other_id, message.id)
    with pytest.raises(InstanceNotFoundError):
        store.steer_queued_message(other_id, message.id)
    with pytest.raises(InstanceNotFoundError):
        store.cancel_queued_message(instance_id, "missing-id")


def test_attachment_roundtrip_and_message_binding(store: LocalStore) -> None:
    instance_id = _register(store)
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

    attachment = store.add_attachment(
        agent_instance_id=instance_id,
        mime_type="image/png",
        data=data,
        original_filename="pixel.png",
    )

    assert attachment.id
    assert attachment.mime_type == "image/png"
    assert attachment.size_bytes == len(data)
    assert attachment.original_filename == "pixel.png"
    assert store.get_attachment(attachment.id) == attachment
    attachment_path = store.get_attachment_path(attachment.id)
    assert attachment_path is not None
    assert attachment_path.read_bytes() == data

    message = store.add_user_message(
        agent_instance_id=instance_id,
        content="",
        mark_as_read=False,
        attachment_ids=[attachment.id],
    )

    metadata = message.message_metadata or {}
    assert metadata["attachments"] == [
        {
            "id": attachment.id,
            "mime_type": "image/png",
            "size_bytes": len(data),
            "width": None,
            "height": None,
            "filename": "pixel.png",
        }
    ]
    bound = store.get_attachment(attachment.id)
    assert bound is not None
    assert bound.message_id == message.id


def test_attachment_cannot_be_bound_twice(store: LocalStore) -> None:
    instance_id = _register(store)
    attachment = store.add_attachment(
        agent_instance_id=instance_id,
        mime_type="image/png",
        data=b"\x89PNG\r\n\x1a\n",
    )
    store.add_user_message(
        agent_instance_id=instance_id,
        content="first",
        mark_as_read=False,
        attachment_ids=[attachment.id],
    )

    with pytest.raises(ValueError, match="Attachment"):
        store.add_user_message(
            agent_instance_id=instance_id,
            content="second",
            mark_as_read=False,
            attachment_ids=[attachment.id],
        )


def test_attachment_must_belong_to_the_instance(store: LocalStore) -> None:
    owner_id = _register(store)
    other_id = _register(store, agent_instance_id="other-instance")
    attachment = store.add_attachment(
        agent_instance_id=owner_id,
        mime_type="image/png",
        data=b"\x89PNG\r\n\x1a\n",
    )

    with pytest.raises(ValueError, match="Attachment"):
        store.add_user_message(
            agent_instance_id=other_id,
            content="wrong instance",
            mark_as_read=False,
            attachment_ids=[attachment.id],
        )

    assert store.get_attachment("missing-attachment") is None


def test_requeue_only_steer_messages(store: LocalStore) -> None:
    instance_id = _register(store)
    steer = store.add_user_message(
        agent_instance_id=instance_id,
        content="steer",
        mark_as_read=False,
        message_metadata={"queue": {"status": "steer"}},
    )
    queued = store.add_user_message(
        agent_instance_id=instance_id,
        content="queued",
        mark_as_read=False,
        message_metadata={"queue": {"status": "queued"}},
    )

    updated = store.requeue_user_message(steer.id)

    assert _queue(updated)["status"] == "queued"
    assert isinstance(_queue(updated)["requeued_at"], str)
    unchanged = store.requeue_user_message(queued.id)
    assert _queue(unchanged) == {"status": "queued"}
    with pytest.raises(InstanceNotFoundError):
        store.requeue_user_message("missing-id")
