<p align="center">
  <img width="200" src="./assets/logo.png" />
</p>

<div align=center>
	<h1>Axo</h1>
</div>

**axo** is the Python library of the Axo platform. Write a class or a function, and run it in
your virtual environment (VE):

- **Active objects**: subclass `Axo`. An object has a name in its VE and keeps its state
  (`self.x`) between calls. Calls on one object run one at a time; a call that fails
  changes nothing.
- **Functions**: mark them with `@axo_function`. Each call is a job.

Requires Python 3.10 or newer.

## Getting started

```bash
pip install axo
export AXO_API_KEY=axo_...        # an API key from the web app (account menu → API keys)
export AXO_VE=<your VE id>
```

```python
from axo import Axo, AxoClient, axo_function, axo_method


class Counter(Axo):
    def __init__(self, start: int = 0):
        self.count = start

    def run(self, by: int = 1):          # "run" is the default method
        self.count += by
        return self.count

    @axo_method                          # other methods callable from outside
    def reset(self):
        self.count = 0


@axo_function(max_concurrency=4)
def add(a, b):
    return a + b


client = AxoClient()
print(client.call(add, a=1, b=2).unwrap().result().unwrap())    # 3

visits = client.create(Counter, "visits", 10).unwrap()
print(visits.run(by=5).unwrap().result().unwrap())              # 15
```

Every client call returns an `option.Result` (`Ok(value)` or `Err(AxoError)`); errors carry
the platform's error name and number (see the error catalog in the web app).

- Arguments are passed by name and must be JSON values. Add `ctx` to a signature to receive
  the job's context (`ctx.job_id`, `ctx.scratch_dir`, `ctx.warn()`).
- Inside an object, `self.event` is what started the call in a choreography, and `self.store`
  reads and writes the buckets it is connected to.
- Object state: small JSON values load with the call; big values, bytes and anything else
  (pickled) load the first time a method reads them. Names starting with `_` are not saved.
- `@axo_function(python_version="3.12", requirements=["numpy"])` runs in its own container;
  `idle_ttl=-1` keeps it warm.
- A new class or function version is saved only when its code changes. Objects stay on their
  version until `handle.upgrade()`, which runs the new class's `upgrade(self, old_version)`.
- A plain call (`add(1, 2)`, `Counter().run()`) runs locally, with no platform involved.

More in `examples/`.

## Development

```bash
poetry install
poetry run pytest
```

<!-- CONTRIBUTING -->
## Contributing

Contributions are what make the open source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**.

If you have a suggestion that would make this better, please fork the repo and create a pull request. You can also simply open an issue with the tag "enhancement".
Don't forget to give the project a star! Thanks again!

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

<p align="right">(<a href="#top">back to top</a>)</p>



<!-- LICENSE -->
## License

Distributed under the MIT License. See `LICENSE.txt` for more information.

<p align="right">(<a href="#top">back to top</a>)</p>



<!-- CONTACT -->
## Contact


 Ignacio Castillo - [@NachoCastillo]() - jesus.castillo.b@cinvestav.mx (Software Architect / Design)

<p align="right">(<a href="#top">back to top</a>)</p>
