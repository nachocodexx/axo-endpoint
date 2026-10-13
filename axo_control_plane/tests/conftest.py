from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import mongomock
import pytest

from axo_control_plane.application.access import AccessResolver, Invalidations
from axo_control_plane.application.authz import Authorizer
from axo_control_plane.application.buckets import Buckets, StorageAccess
from axo_control_plane.application.sdk_keys import SdkKeys
from axo_control_plane.application.sharing import Sharing
from axo_control_plane.application.capacity import CapacityLedger
from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.notifications import Notifications
from axo_control_plane.application.people import Actor, Names, People
from axo_control_plane.application.ports import InMemoryHistory
from axo_control_plane.application.provisioner import Provisioner, StackSettings
from axo_control_plane.application.teams import Teams
from axo_control_plane.application.ve_service import PurchaseRequest, VeService
from axo_control_plane.domain.plans import Resources
from axo_control_plane.infrastructure.memory import (
    FakeStorage,
    FakeVem,
    InMemoryIdentity,
    InMemoryLeases,
    InMemoryStackDriver,
)


class Clock:
    def __init__(self, t: float = 1_760_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@dataclass
class Platform:
    db: Any
    clock: Clock
    history: InMemoryHistory
    identity: InMemoryIdentity
    invalidations: Invalidations
    access: AccessResolver
    authz: Authorizer
    names: Names
    people: People
    teams: Teams
    notifications: Notifications
    catalog: PlanCatalog
    capacity: CapacityLedger
    ves: VeService
    driver: InMemoryStackDriver
    vem: FakeVem
    leases: InMemoryLeases
    provisioner: Provisioner
    storage: FakeStorage
    buckets: Buckets
    storage_access: StorageAccess
    sharing: Sharing
    keys: SdkKeys

    def user(self, username: str, password: str = "password123") -> Actor:
        """Sign up + log in; returns the Actor the front door would build."""
        self.people.signup(username, f"{username}@example.com", password)
        session = self.people.login(username, password)
        user = self.people.verify(session["access_token"], session["temporal_secret"])
        return self.people.actor_for(user)

    def buy(self, actor: Actor, name: str = "ml", plan_id: str = "basic", **kw) -> dict:
        request = PurchaseRequest(name=name, plan_id=plan_id, quota_cpu_cores=kw.pop("cpu", 4.0),
                                  quota_memory_mb=kw.pop("mem", 8192), **kw)
        return self.ves.buy(actor, request)


@pytest.fixture
def platform() -> Platform:
    db = mongomock.MongoClient()["axo_cp_test"]
    clock = Clock()
    history = InMemoryHistory()
    identity = InMemoryIdentity()
    invalidations = Invalidations(db, now_fn=clock)
    access = AccessResolver(identity, invalidations, ttl_s=300.0, now_fn=clock)
    authz = Authorizer(access)
    names = Names(db)
    people = People(db, identity, access, names, history, now_fn=clock)
    people.bootstrap_admin("admin", "admin-password", "admin@axo.local")
    teams = Teams(db, identity, access, names, history, now_fn=clock)
    notifications = Notifications(db, now_fn=clock)
    catalog = PlanCatalog(db, history, now_fn=clock)
    catalog.seed()
    capacity = CapacityLedger(db, Resources(32.0, 65536))
    ves = VeService(db, catalog, capacity, identity, access, authz, teams, notifications, history,
                    max_custom=Resources(8.0, 16384), now_fn=clock)
    driver = InMemoryStackDriver()
    vem = FakeVem()
    leases = InMemoryLeases()
    settings = StackSettings(
        vem_image="axo-vem:test", endpoint_image="axo-endpoint:test", kurrent_image="kurrent:test",
        mongo_image="mongo:test", control_network="axo-control-net", storage_container="axo-storage",
        storage_uri="tcp://axo-storage:7000", cp_intake_uri="tcp://cp:9100",
        vem_token_fn=lambda ve_id: f"vem-token-{ve_id}", storage_token_fn=lambda ve_id: f"st-{ve_id}",
    )
    provisioner = Provisioner(db, driver, vem, leases, capacity, catalog, identity, notifications, history,
                              settings, instance_id="cp-1", now_fn=clock)
    storage = FakeStorage()
    buckets = Buckets(db, storage, identity, access, authz, names, teams, history,
                      default_quota_bytes=10 * 1024 ** 3, max_quota_bytes=1024 ** 4, now_fn=clock)
    sharing = Sharing(db, people, names, teams, buckets, identity, access, authz, notifications, history,
                      now_fn=clock)
    keys = SdkKeys(db, people, authz, history, pepper="test-pepper", now_fn=clock)
    return Platform(db, clock, history, identity, invalidations, access, authz, names, people, teams,
                    notifications, catalog, capacity, ves, driver, vem, leases, provisioner, storage,
                    buckets, StorageAccess(buckets, access), sharing, keys)
