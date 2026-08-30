"""Intelligence routes: forecast, decisions, what-if, natural-language console."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from nexus.api.deps import get_runtime
from nexus.api.schemas import (
    DecisionActionRequest,
    DecisionModel,
    DecisionRequest,
    Forecast,
    NLQRequest,
    NLQResponse,
    WhatIfPreset,
    WhatIfRequest,
    WhatIfResult,
)
from nexus.nlq.service import NLQService
from nexus.runtime.live import LiveRuntime
from nexus.whatif.presets import PRESETS

router = APIRouter(tags=["intelligence"])


@router.get("/forecast", response_model=Forecast)
async def forecast(
    horizon_min: int = Query(90, ge=5, le=480), rt: LiveRuntime = Depends(get_runtime)
) -> Forecast:
    return await run_in_threadpool(rt.forecast, horizon_min)


@router.post("/decisions", response_model=DecisionModel)
async def decide(body: DecisionRequest, rt: LiveRuntime = Depends(get_runtime)) -> DecisionModel:
    if rt.ops.busy:
        raise HTTPException(409, "a decision is already being computed")
    return await run_in_threadpool(
        rt.decide, body.goal, body.trigger, body.horizon_min, body.candidates, body.use_llm, body.context
    )


@router.get("/decisions", response_model=list[DecisionModel])
def decisions(
    limit: int = Query(20, ge=1, le=200), rt: LiveRuntime = Depends(get_runtime)
) -> list[DecisionModel]:
    return rt.ops.history(limit)


@router.get("/decisions/{decision_id}", response_model=DecisionModel)
def decision(decision_id: str, rt: LiveRuntime = Depends(get_runtime)) -> DecisionModel:
    d = rt.ops.get(decision_id)
    if d is None:
        raise HTTPException(404, "decision not found")
    return d


@router.post("/decisions/{decision_id}/actions", response_model=DecisionModel)
async def decision_action(
    decision_id: str, body: DecisionActionRequest, rt: LiveRuntime = Depends(get_runtime)
) -> DecisionModel:
    try:
        if body.action == "approve":
            return await run_in_threadpool(rt.ops.approve, decision_id, body.actor, body.plan_id)
        if body.action == "reject":
            return await run_in_threadpool(rt.ops.reject, decision_id, body.actor, body.note)
        return await run_in_threadpool(rt.ops.execute, decision_id, body.actor)
    except KeyError as exc:
        raise HTTPException(404, "decision not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/whatif", response_model=WhatIfResult)
def whatif_submit(body: WhatIfRequest, rt: LiveRuntime = Depends(get_runtime)) -> WhatIfResult:
    known = {s["name"] for s in rt.strategies()}
    unknown = [s for s in body.strategies if s not in known]
    if unknown:
        raise HTTPException(400, f"unknown strategies: {unknown}")
    return rt.whatif.submit(body)


@router.post("/whatif/run", response_model=WhatIfResult)
async def whatif_run(body: WhatIfRequest, rt: LiveRuntime = Depends(get_runtime)) -> WhatIfResult:
    """Synchronous variant (waits for the result)."""
    return await run_in_threadpool(rt.whatif_run, body)


@router.get("/whatif", response_model=list[WhatIfResult])
def whatif_list(
    limit: int = Query(20, ge=1, le=100), rt: LiveRuntime = Depends(get_runtime)
) -> list[WhatIfResult]:
    return rt.whatif.history(limit)


@router.get("/whatif/presets", response_model=list[WhatIfPreset])
def whatif_presets() -> list[WhatIfPreset]:
    return PRESETS


@router.get("/whatif/{result_id}", response_model=WhatIfResult)
def whatif_get(result_id: str, rt: LiveRuntime = Depends(get_runtime)) -> WhatIfResult:
    r = rt.whatif.get(result_id)
    if r is None:
        raise HTTPException(404, "what-if result not found")
    return r


@router.post("/nlq", response_model=NLQResponse)
async def nlq(body: NLQRequest, rt: LiveRuntime = Depends(get_runtime)) -> NLQResponse:
    return await run_in_threadpool(NLQService(rt).ask, body.question, body.horizon_min, body.use_llm)
