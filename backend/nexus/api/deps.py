from __future__ import annotations

from fastapi import Request

from nexus.runtime.live import LiveRuntime


def get_runtime(request: Request) -> LiveRuntime:
    rt = getattr(request.app.state, "runtime", None)
    if rt is None:
        raise RuntimeError("runtime not initialised")
    return rt
