"""Classes and functions the tests send around, defined in a real module
(like a user's script) so their source can be captured."""

import math

from axo import Axo, axo_function, axo_method

RATE = 3


class Counter(Axo):
    def __init__(self, start: int = 0):
        self.count = start
        self.history = []

    def run(self, by: int = 1):
        self.count += by
        self.history.append(by)
        return self.count

    @axo_method
    def reset(self, ctx):
        ctx.warn("reset")
        self.count = 0
        return "done"

    @axo_method(name="sqrt")
    def square_root(self):
        return math.sqrt(self.count)

    def helper(self):
        return "not callable from outside"


class UsesGlobal(Axo):
    def run(self):
        return RATE


class Versioned(Axo):
    def upgrade(self, old_version):
        self.migrated_from = old_version
        self.total = self.count * 10


@axo_function(requirements=["numpy"], max_concurrency=4, idle_ttl=-1)
def add(a, b, ctx=None):
    return a + b


@axo_function
def scale(x):
    return x * RATE
