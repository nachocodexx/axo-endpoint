"""Axo classes the object tests register, in a real module so their source
can be captured."""

from axo import Axo, axo_method


class Ledger(Axo):
    def __init__(self, owner: str = "nobody"):
        self.owner = owner
        self.entries = []
        self.blob = b""

    def run(self, amount: int = 1):
        self.entries.append(amount)
        return sum(self.entries)

    @axo_method
    def attach(self, size: int = 0):
        self.blob = b"x" * size
        return len(self.blob)

    @axo_method
    def blob_size(self):
        return len(self.blob)

    @axo_method
    def explode(self):
        self.entries.append(999)
        raise RuntimeError("boom")

    @axo_method
    def event_seen(self, tag: str = "none"):
        return {"tag": tag, "event": self.event}

    @axo_method
    def forget(self):
        del self.owner
        return "gone"
