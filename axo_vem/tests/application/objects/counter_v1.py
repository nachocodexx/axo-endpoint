from axo import Axo, axo_method


class Counter(Axo):
    def __init__(self, start: int = 0):
        self.count = start

    def run(self, by: int = 1):
        self.count += by
        return self.count

    @axo_method
    def explode(self):
        self.count = -1
        raise RuntimeError("boom")
