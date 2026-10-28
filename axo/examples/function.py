"""A function run as a job; a new version is saved only when its code changes.

    AXO_API_KEY=axo_... AXO_VE=<ve id> python examples/function.py
"""

from axo import AxoClient, Context, axo_function


@axo_function(max_concurrency=4)
def word_count(text: str, ctx: Context = None):
    words = len(text.split())
    if words == 0:
        ctx.warn("empty text")
    return {"words": words}


if __name__ == "__main__":
    print(word_count(text="runs here too"))                   # a plain call runs locally
    with AxoClient() as client:
        job = client.call(word_count, text="and this one runs on the platform").unwrap()
        print(job.result().unwrap())
