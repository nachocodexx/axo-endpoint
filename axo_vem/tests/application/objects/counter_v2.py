from axo import Axo


class Counter(Axo):
    def __init__(self, start: int = 0):
        self.total = start

    def run(self, by: int = 1):
        self.total += by
        return self.total

    def upgrade(self, old_version):
        self.total = self.count * 10
        del self.count
