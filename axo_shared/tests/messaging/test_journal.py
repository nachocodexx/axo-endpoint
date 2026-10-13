import pytest

from axo_shared.messaging import InMemoryJournal, OutboxMessage, SqliteJournal


def _msg(message_id: str, next_at: float = 0.0, created_at: float = 0.0) -> OutboxMessage:
    return OutboxMessage(
        message_id=message_id,
        event_type="JobAttemptRecorded",
        source_id="ep-0",
        data={"event_id": message_id, "nested": {"n": 1}},
        created_at=created_at,
        next_attempt_at=next_at,
    )


@pytest.fixture(params=["memory", "sqlite"])
def journal(request, tmp_path):
    if request.param == "memory":
        j = InMemoryJournal()
    else:
        j = SqliteJournal(str(tmp_path / "outbox.db"))
    yield j
    j.close()


def test_append_is_idempotent_on_message_id(journal):
    assert journal.append(_msg("a")).unwrap() is True
    assert journal.append(_msg("a")).unwrap() is False
    assert journal.size().unwrap() == 1


def test_due_returns_only_ready_messages_oldest_due_first(journal):
    journal.append(_msg("late", next_at=50.0))
    journal.append(_msg("second", next_at=10.0, created_at=2.0))
    journal.append(_msg("first", next_at=10.0, created_at=1.0))
    journal.append(_msg("earliest", next_at=5.0))

    due = journal.due(now=20.0, limit=10).unwrap()
    assert [m.message_id for m in due] == ["earliest", "first", "second"]
    assert journal.due(now=20.0, limit=2).unwrap()[-1].message_id == "first"


def test_due_round_trips_message_data(journal):
    journal.append(_msg("a"))
    (message,) = journal.due(now=1.0, limit=1).unwrap()
    assert message == _msg("a")


def test_reschedule_counts_the_attempt_and_moves_due_time(journal):
    journal.append(_msg("a"))
    journal.reschedule("a", 100.0).unwrap()
    assert journal.due(now=99.0, limit=10).unwrap() == []
    (message,) = journal.due(now=100.0, limit=10).unwrap()
    assert message.attempts == 1
    assert message.next_attempt_at == 100.0


def test_reschedule_of_an_already_acked_message_is_not_an_error(journal):
    assert journal.reschedule("gone", 1.0).is_ok


def test_ack_removes_and_reports_how_many(journal):
    for message_id in ("a", "b", "c"):
        journal.append(_msg(message_id))
    assert journal.ack(["a", "c", "unknown"]).unwrap() == 2
    assert journal.size().unwrap() == 1
    assert journal.ack([]).unwrap() == 0


def test_sqlite_journal_survives_reopening(tmp_path):
    path = str(tmp_path / "outbox.db")
    first = SqliteJournal(path)
    first.append(_msg("a"))
    first.append(_msg("b"))
    first.ack(["a"])
    first.close()

    reopened = SqliteJournal(path)
    assert [m.message_id for m in reopened.due(now=1.0, limit=10).unwrap()] == ["b"]
    reopened.close()


def test_sqlite_journal_reports_unserializable_data_as_err(tmp_path):
    journal = SqliteJournal(str(tmp_path / "outbox.db"))
    circular = {}
    circular["self"] = circular
    result = journal.append(OutboxMessage(message_id="y", event_type="T", source_id="s", data=circular))
    assert result.is_err
    assert journal.size().unwrap() == 0
    journal.close()
