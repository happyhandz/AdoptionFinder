"""Tiny pub/sub bridging background threads to SSE clients."""
import asyncio
import json

_loop = None
_subs = set()


def bind_loop(loop):
    global _loop
    _loop = loop


def subscribe():
    q = asyncio.Queue(maxsize=100)
    _subs.add(q)
    return q


def unsubscribe(q):
    _subs.discard(q)


def publish(event, data=None):
    """Thread-safe."""
    if _loop is None:
        return
    msg = json.dumps({"event": event, "data": data or {}})

    def _put():
        for q in list(_subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    _loop.call_soon_threadsafe(_put)
