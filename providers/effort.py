"""
effort.py — how demanding the call in progress is, carried without threading
a new argument through every layer between the caller and the model.

The owner's rule for the Claude model on this machine:

    "if the tasks are simple program can use sonnet and if tasks are complex
     the program can use opus 4.8 — not the higher model than opus 4.8"

The call that knows a task is demanding is several layers above the one that
picks the model (caller -> routing -> client -> local_cli), and those layers
already have fixed signatures used everywhere. A context variable carries the
fact down instead: set around the demanding call, read at the bottom, gone
when the call returns. Nothing else in the stack has to know it exists.

It is set by routing.deliberate(), which this codebase already defines as
"the handful of judgements where being wrong costs real money" — how the MD
allocates the day, and whether Security clears something to be acted on. Any
other caller can use `demanding()` for a job it knows is heavy.
"""
import contextlib
import contextvars

_WHY = contextvars.ContextVar("demanding_because", default="")


@contextlib.contextmanager
def demanding(why: str):
    """Mark everything inside as a demanding task, and say why."""
    token = _WHY.set(str(why or "marked demanding by its caller"))
    try:
        yield
    finally:
        _WHY.reset(token)


def why_demanding() -> str:
    """Why the call in progress was marked demanding, or '' if it was not."""
    return _WHY.get()
