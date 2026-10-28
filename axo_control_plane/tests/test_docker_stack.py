"""DockerStackDriver against a stub Docker client (no Docker needed)."""

import docker.errors
import pytest

from axo_control_plane.application.ports import ContainerRun
from axo_control_plane.domain.errors import UnavailableError
from axo_control_plane.infrastructure.docker_stack import DockerStackDriver


class _Images:
    def __init__(self, present, pullable):
        self.present, self.pullable, self.pulled = set(present), set(pullable), []

    def get(self, image):
        if image not in self.present:
            raise docker.errors.ImageNotFound(image)

    def pull(self, image):
        if image not in self.pullable:
            raise docker.errors.APIError(f"pull access denied for {image}")
        self.pulled.append(image)
        self.present.add(image)


class _Container:
    started = False

    def start(self):
        self.started = True


class _Containers:
    def __init__(self, images):
        self.images, self.created = images, []

    def create(self, image, **_):
        if image not in self.images.present:
            raise docker.errors.ImageNotFound(image)
        self.created.append(image)
        return _Container()


class _Client:
    def __init__(self, present=(), pullable=()):
        self.images = _Images(present, pullable)
        self.containers = _Containers(self.images)


def _spec(image):
    return ContainerRun(name="ve-x-redis", image=image, networks=[])


def test_a_missing_image_is_pulled_before_the_container_is_created():
    client = _Client(pullable={"redis:8-alpine"})
    DockerStackDriver(client=client).run(_spec("redis:8-alpine"))
    assert client.images.pulled == ["redis:8-alpine"]
    assert client.containers.created == ["redis:8-alpine"]


def test_a_present_image_is_not_pulled_again():
    client = _Client(present={"axo-vem:local"})
    DockerStackDriver(client=client).run(_spec("axo-vem:local"))
    assert client.images.pulled == []


def test_a_local_only_image_that_was_never_built_says_so():
    client = _Client()
    with pytest.raises(UnavailableError, match="axo-vem:local"):
        DockerStackDriver(client=client).run(_spec("axo-vem:local"))
    assert client.containers.created == []
