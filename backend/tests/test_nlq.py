import pytest

from nexus.events.types import EventType
from nexus.llm.client import NullLLM
from nexus.llm.rag import SOPRetriever
from nexus.nlq import NLQService, attribute_delay, build_scenario, classify, explain_text, extract_params
from nexus.runtime.live import LiveRuntime


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("Why are orders slowing down?", "explain"),
        ("What happens if tomorrow's order volume increases by 40%?", "whatif"),
        ("What if we remove two robots?", "whatif"),
        ("Suppose zone B is inaccessible for an hour", "whatif"),
        ("How many orders are open right now?", "status"),
        ("Forecast the next 60 minutes", "forecast"),
        ("What should we do about zone C congestion?", "recommend"),
        ("Where is R03 and what is it doing?", "entity"),
        ("Tell me about ORD-000012", "entity"),
        ("banana", "unknown"),
    ],
)
def test_classify(question, intent):
    assert classify(question)[0] == intent


def test_extract_params():
    p = extract_params("What if order volume increases by 40% and R07 fails for 30 minutes?")
    assert (
        p["multiplier"] == 1.4
        and p["robot_ids"] == ["R07"]
        and p["failure"]
        and p["minutes"] == 30
        and p["demand"]
    )
    assert extract_params("remove two robots")["robots"] == 2
    assert extract_params("what if demand doubles")["multiplier"] == 2.0
    assert extract_params("what if demand drops 20%")["multiplier"] == pytest.approx(0.8)
    assert extract_params("close dock 2")["dock"] == "D2"
    assert extract_params("move inventory from zone C to zone B")["from_zone"] == "C"


def test_build_scenario(small_world):
    sc = build_scenario(
        extract_params("what if we remove two robots and demand doubles for 15 minutes"), small_world
    )
    types = [m.type for m in sc.mutations]
    assert "REMOVE_ROBOTS" in types and "DEMAND_BURST" in types
    sc2 = build_scenario(extract_params("what if R07 fails"), small_world)
    assert sc2.mutations[0].type == "ROBOT_FAILURE" and sc2.mutations[0].params["robot_ids"] == ["R07"]
    sc3 = build_scenario(extract_params("what if charging capacity is halved"), small_world)
    assert sc3.mutations[0].type == "DISABLE_CHARGERS"
    sc4 = build_scenario({}, small_world)
    assert sc4.mutations[0].type == "ROBOT_FAILURE"


def test_attribution_and_explanation(tiny_engine):
    eng = tiny_engine
    eng.run(600)
    eng.inject(EventType.ROBOT_FAILURE, "R01", {"cause": "motor_fault", "recovery_ticks": 900}, origin="user")
    eng.inject(EventType.ZONE_CLOSED, "A", {"reason": "spill"}, origin="user")
    eng.run(60)
    a = attribute_delay(eng.world)
    causes = {c["cause"] for c in a["causes"]}
    assert "robot_unavailable" in causes and "zone_closed" in causes
    assert abs(sum(c["share"] for c in a["causes"]) - 1.0) < 1e-6
    text = explain_text(a)
    assert "contributor" in text and "%" in text


def test_sop_retriever():
    r = SOPRetriever()
    top = r.retrieve("robot failure reassign tasks", k=2)
    assert top and top[0][0].id == "sop-robot-failure"
    assert r.snippets("congestion routing")[0].startswith("Zone congestion")


def test_nlq_service_end_to_end():
    rt = LiveRuntime(scale="tiny", seed=2, strategy="optimized", llm=NullLLM(), workers=1)
    rt.step(400)
    svc = NLQService(rt)
    status = svc.ask("How many orders are open right now?")
    assert status.intent == "status" and "open orders" in status.answer and not status.llm_used
    why = svc.ask("Why are orders slowing down?")
    assert why.intent == "explain" and "attribution" in why.data
    ent = svc.ask("Where is R01 and what is it doing?")
    assert ent.intent == "entity" and "R01" in ent.answer
    fc = svc.ask("Forecast the next 30 minutes", horizon_min=30)
    assert fc.intent == "forecast" and fc.data["forecast"]["demand"]["horizon_min"] == 30
    wi = svc.ask("What if we remove one robot?", horizon_min=5)
    assert wi.intent == "whatif" and wi.data["whatif"]["status"] == "done" and "SLA breach" in wi.answer
    unknown = svc.ask("banana")
    assert unknown.intent == "unknown" and unknown.suggestions
    rt.close()
