import axo_shared.storage as shared
from axo_endpoint.core import storage as shim
from axo_endpoint.core.storage.backend import StorageBackend, StorageKey
from axo_endpoint.core.storage.filesystem import FsKey
from axo_endpoint.core.storage.in_memory import InMemoryStorageBackend


def test_package_reexports_the_shared_classes():
    for name in shared.__all__:
        assert getattr(shim, name) is getattr(shared, name)


def test_submodule_paths_resolve_to_the_shared_classes():
    assert StorageBackend is shared.StorageBackend
    assert StorageKey is shared.StorageKey
    assert FsKey is shared.FsKey
    assert InMemoryStorageBackend is shared.InMemoryStorageBackend
