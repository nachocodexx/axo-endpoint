from axo_endpoint.core.dataio import IORef


def test_to_dict_from_dict_round_trip():
    ref = IORef(bucket="sales", name="2024/q1.csv", version=3, format="csv")
    d = ref.to_dict()
    assert d == {"bucket": "sales", "name": "2024/q1.csv", "version": 3, "format": "csv", "chunk_index": None}
    assert IORef.from_dict(d) == ref


def test_defaults_mean_latest_version_and_raw_format():
    ref = IORef.from_dict({"bucket": "b", "name": "x"})
    assert ref.version is None and ref.format == "raw" and ref.chunk_index is None


def test_chunk_index_round_trips():
    ref = IORef(bucket="b", name="x", version=1, chunk_index=3)
    assert IORef.from_dict(ref.to_dict()) == ref
