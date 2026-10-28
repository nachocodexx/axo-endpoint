from axo_shared.active_objects.models import (
    AttributeEntry,
    ClassVersionRecord,
    MethodInfo,
    StateManifest,
)


def test_a_manifest_round_trips_and_counts_its_size():
    m = StateManifest(object_id="o1", revision=3, updated_at=5.0, attributes={
        "n": AttributeEntry(tier="hot", kind="json", size=2, hash="a" * 64),
        "blob": AttributeEntry(tier="blob", kind="bytes", size=100, hash="b" * 64)})
    assert m.size_bytes == 102
    assert StateManifest.from_dict(m.to_dict()) == m


def test_a_class_version_lists_its_callable_methods():
    record = ClassVersionRecord(class_name="Counter", version=2, code_hash="h", methods=[
        MethodInfo(name="run", params=["by"]), MethodInfo(name="reset", wants_context=True)])
    assert record.method("reset").wants_context and record.method("nope") is None
    assert ClassVersionRecord.from_dict(record.to_dict()) == record
