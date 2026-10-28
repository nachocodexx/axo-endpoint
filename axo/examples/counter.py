"""An active object: its state lives on the platform between calls.

    AXO_API_KEY=axo_... AXO_VE=<ve id> python examples/counter.py
"""

from axo import Axo, AxoClient, axo_method


class Counter(Axo):
    def __init__(self, start: int = 0):
        self.count = start
        self.history = []

    def run(self, by: int = 1):
        self.count += by
        self.history.append(by)
        return self.count

    @axo_method
    def reset(self):
        self.count = 0
        return self.count


if __name__ == "__main__":
    with AxoClient() as client:
        visits = client.create(Counter, "visits", 10).unwrap()
        visits.created.result().unwrap()
        print(visits.run(by=5).unwrap().result().unwrap())      # 15
        print(visits.call("reset").unwrap().result().unwrap())  # 0
