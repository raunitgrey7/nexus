"""NEXUS command-line interface: ``uv run nexus --help``."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from nexus.core.logging import configure_logging

app = typer.Typer(
    help="NEXUS — AI-native digital twin & autonomous operations platform", no_args_is_help=True
)
if sys.platform == "win32":  # make the Windows console UTF-8 capable for tables / glyphs
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
console = Console(highlight=False, legacy_windows=False)


@app.command()
def api(
    host: str = "0.0.0.0",
    port: int = 8000,
    reload: bool = False,
    strategy: str = "optimized",
    scale: str | None = None,
    seed: int | None = None,
) -> None:
    """Run the API + live twin."""
    import uvicorn

    if scale or seed is not None:
        from nexus.core.config import settings

        if scale:
            settings.default_scale = scale
        if seed is not None:
            settings.default_seed = seed
    if reload:
        uvicorn.run("nexus.api.app:create_app", factory=True, host=host, port=port, reload=True)
    else:
        from nexus.api.app import create_app

        uvicorn.run(create_app(strategy=strategy), host=host, port=port)


@app.command()
def run(
    scale: str = "small",
    minutes: int = 120,
    strategy: str = "optimized",
    seed: int = 42,
    fail_robot: str | None = None,
    fail_at_min: int = 30,
) -> None:
    """Run a headless simulation and print the KPIs."""
    from nexus.events.types import EventType
    from nexus.simulation.engine import SimulationEngine
    from nexus.simulation.faults import FaultInjector, ScheduledFault
    from nexus.simulation.strategies import make_strategy
    from nexus.twin import build_world, spec_for

    configure_logging("WARNING")
    world = build_world(spec_for(scale, seed=seed))
    faults = (
        [
            ScheduledFault(
                fail_at_min * 60,
                EventType.ROBOT_FAILURE,
                fail_robot,
                {"cause": "motor_fault", "recovery_ticks": 2700},
            )
        ]
        if fail_robot
        else []
    )
    eng = SimulationEngine(world, make_strategy(strategy), fault_injector=FaultInjector(faults))
    t0 = time.perf_counter()
    eng.run(minutes * 60)
    dt = time.perf_counter() - t0
    k = eng.kpis()
    table = Table(
        title=f"NEXUS · {scale} · {strategy} · {minutes} min · seed {seed}  ({minutes * 60 / dt:,.0f} ticks/s)"
    )
    table.add_column("KPI")
    table.add_column("Value", justify="right")
    for key, value in k.to_dict().items():
        table.add_row(key, f"{value:.4f}" if isinstance(value, float) else str(value))
    console.print(table)


@app.command()
def decide(
    scale: str = "small",
    warmup_min: int = 60,
    fail_robot: str = "R07",
    horizon_min: int = 90,
    candidates: int = 8,
    llm: bool = False,
    seed: int = 42,
    out: Path | None = None,
) -> None:
    """Warm up a twin, fail a robot, and run the full decision pipeline once."""
    from nexus.agents.ops_manager import OperationsManager
    from nexus.events.types import EventType
    from nexus.forecasting import Forecaster, HistoryRecorder
    from nexus.llm.client import LLMClient, NullLLM
    from nexus.simulation.engine import SimulationEngine
    from nexus.simulation.faults import FaultInjector
    from nexus.simulation.strategies import make_strategy
    from nexus.twin import build_world, spec_for

    configure_logging("WARNING")
    world = build_world(spec_for(scale, seed=seed))
    eng = SimulationEngine(world, make_strategy("optimized"), fault_injector=FaultInjector())
    rec = HistoryRecorder()
    eng.hooks.append(rec.hook)
    console.print(f"[cyan]warming up {warmup_min} min…[/cyan]")
    eng.run(warmup_min * 60)
    eng.inject(
        EventType.ROBOT_FAILURE, fail_robot, {"cause": "motor_fault", "recovery_ticks": 2700}, origin="user"
    )
    eng.run(30)
    ops = OperationsManager(
        eng,
        LLMClient() if llm else NullLLM(),
        Forecaster(),
        rec,
        candidate_plans=candidates,
        horizon_ticks=horizon_min * 60,
    )
    console.print(f"[cyan]{fail_robot} failed — deciding…[/cyan]")
    d = ops.decide(trigger=f"ROBOT_FAILURE:{fail_robot}")
    _print_decision(d)
    if out:
        out.write_text(d.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"saved → {out}")


def _print_decision(d) -> None:  # type: ignore[no-untyped-def]
    table = Table(
        title=f"Decision {d.id} · trigger {d.trigger} · {d.candidates_evaluated} candidates · {d.timings.get('total_ms', 0):.0f} ms"
    )
    for col in ("#", "plan", "source", "SLA breach", "avg ft (min)", "thr/h", "cong", "score", "risk"):
        table.add_column(col, justify="right" if col not in ("plan", "source", "risk") else "left")
    base = d.baseline
    if base:
        table.add_row(
            "—",
            "Do nothing (baseline)",
            "—",
            f"{base.kpis.sla_breach_rate_projected:.1%}",
            f"{base.kpis.avg_fulfillment_min:.2f}",
            f"{base.kpis.throughput_per_hour:.0f}",
            f"{base.kpis.congestion_index:.2f}",
            f"{base.score:.2f}",
            "",
        )
    for p in sorted(d.candidates, key=lambda p: p.rank or 99):
        s = p.simulation
        mark = "* " if p.id == d.recommended_plan_id else ""
        table.add_row(
            str(p.rank or "-"),
            mark + p.name,
            p.source,
            f"{s.kpis.sla_breach_rate_projected:.1%}" if s else "-",
            f"{s.kpis.avg_fulfillment_min:.2f}" if s else "-",
            f"{s.kpis.throughput_per_hour:.0f}" if s else "-",
            f"{s.kpis.congestion_index:.2f}" if s else "-",
            f"{s.score:.2f}" if s else "-",
            p.risk.level if p.risk else "",
        )
    console.print(table)
    console.print(f"[bold]{d.explanation}[/bold]")
    console.print(f"status: {d.status} · approval: {d.approval.reason}")


@app.command()
def whatif(
    preset: str = "demand-plus-40",
    scale: str = "small",
    warmup_min: int = 45,
    horizon_min: int = 60,
    seed: int = 42,
) -> None:
    """Run a what-if preset against a warmed-up twin."""
    from nexus.api.schemas import WhatIfRequest
    from nexus.simulation.engine import SimulationEngine
    from nexus.simulation.strategies import make_strategy
    from nexus.twin import build_world, spec_for
    from nexus.whatif.engine import WhatIfEngine
    from nexus.whatif.presets import preset_by_id

    configure_logging("WARNING")
    p = preset_by_id(preset)
    if p is None:
        console.print(f"[red]unknown preset {preset}[/red]")
        raise typer.Exit(1)
    world = build_world(spec_for(scale, seed=seed))
    eng = SimulationEngine(world, make_strategy("optimized"))
    eng.run(warmup_min * 60)
    result = WhatIfEngine(lambda: eng).run(
        WhatIfRequest(
            scenario=p.scenario, strategies=["baseline", "optimized", "nexus_full"], horizon_min=horizon_min
        )
    )
    table = Table(title=f"What-if · {p.question}")
    for col in ("strategy", "SLA breach", "avg ft", "p95", "thr/h", "util", "cong", "score"):
        table.add_column(col, justify="right" if col != "strategy" else "left")
    if result.reference:
        r = result.reference.kpis
        table.add_row(
            "reference (no scenario)",
            f"{r.sla_breach_rate_projected:.1%}",
            f"{r.avg_fulfillment_min:.2f}",
            f"{r.p95_fulfillment_min:.2f}",
            f"{r.throughput_per_hour:.0f}",
            f"{r.robot_utilization:.0%}",
            f"{r.congestion_index:.2f}",
            "",
        )
    for row in result.comparison:
        table.add_row(
            ("* " if row["strategy"] == result.best_strategy else "") + row["strategy"],
            f"{row['sla_breach_rate_projected']:.1%}",
            f"{row['avg_fulfillment_min']:.2f}",
            f"{row['p95_fulfillment_min']:.2f}",
            f"{row['throughput_per_hour']:.0f}",
            f"{row['robot_utilization']:.0%}",
            f"{row['congestion_index']:.2f}",
            f"{row['score']:.2f}",
        )
    console.print(table)
    console.print(result.narrative)


@app.command()
def demo(
    scale: str = "small", seed: int = 42, llm: bool = False, surge: float = 1.2, horizon_min: int = 60
) -> None:
    """The pitch storyline: live twin → late-morning peak → demand surge → R07 fails → NEXUS simulates → best plan."""
    from nexus.agents.ops_manager import OperationsManager
    from nexus.events.types import EventType
    from nexus.forecasting import Forecaster, HistoryRecorder
    from nexus.llm.client import LLMClient, NullLLM
    from nexus.simulation.engine import SimulationEngine
    from nexus.simulation.faults import FaultInjector
    from nexus.simulation.strategies import make_strategy
    from nexus.twin import build_world, spec_for

    configure_logging("WARNING")
    world = build_world(spec_for(scale, seed=seed))
    console.rule("[bold cyan]NEXUS · live digital twin")
    console.print(
        f"{world.name}: {len(world.robots)} robots · {len(world.storage_zones())} storage zones · {len(world.docks)} docks · "
        f"{world.inventory_units():,} units · ≈{world.demand.orders_per_hour * 10:,.0f} orders/day"
    )
    eng = SimulationEngine(world, make_strategy("optimized"), fault_injector=FaultInjector())
    rec = HistoryRecorder()
    eng.hooks.append(rec.hook)
    console.print("[dim]fast-forwarding to the late-morning peak…[/dim]")
    for _ in range(5):
        eng.run(29 * 60)
        k = eng.kpis(since_tick=max(0, world.clock.tick - 1800))
        console.print(
            f"  {world.clock.now():%H:%M}  open={k.orders_open:3d}  delivered(30m)={k.orders_delivered:4d}  "
            f"breach={k.sla_breach_rate_projected:5.1%}  util={k.robot_utilization:4.0%}  congestion={k.congestion_index:.2f}"
        )
    console.rule(f"[bold yellow]{world.clock.now():%H:%M} demand surge ×{surge:.1f} for 60 min")
    eng.inject(
        EventType.DEMAND_CHANGED, None, {"burst_multiplier": surge, "burst_ticks": 3600}, origin="user"
    )
    eng.run(300)
    console.rule(f"[bold red]{world.clock.now():%H:%M} incident: robot R07 fails (motor fault)")
    eng.inject(
        EventType.ROBOT_FAILURE, "R07", {"cause": "motor_fault", "recovery_ticks": 2700}, origin="user"
    )
    eng.run(30)
    ops = OperationsManager(
        eng, LLMClient() if llm else NullLLM(), Forecaster(), rec, horizon_ticks=horizon_min * 60
    )
    console.print(
        "[cyan]A normal system says: 'Robot R07 offline'. NEXUS simulates the alternatives first…[/cyan]"
    )
    d = ops.decide(trigger="ROBOT_FAILURE:R07")
    _print_decision(d)
    if d.status == "proposed":
        console.print(
            "[yellow]Risk above the auto-approval limit — an operator approves the recommended plan.[/yellow]"
        )
        ops.approve(d.id, actor="demo-operator")
    ops.execute(d.id, actor="demo-operator")
    console.rule("[bold green]plan executed on the live twin")
    for _ in range(4):
        eng.run(15 * 60)
        k = eng.kpis(since_tick=d.created_tick)
        console.print(
            f"  {world.clock.now():%H:%M}  open={k.orders_open:3d}  breach(since decision)={k.sla_breach_rate_projected:5.1%}  "
            f"avg ft={k.avg_fulfillment_min:4.1f} min  util={k.robot_utilization:4.0%}"
        )
    console.print(
        "[dim]Only after simulation and safety validation would this plan be eligible for execution against a real system.[/dim]"
    )


@app.command()
def bench(
    scale: list[str] = typer.Option(["small"], "--scale"),
    minutes: int = 120,
    seeds: int = 3,
    out: Path | None = None,
    workers: int | None = None,
) -> None:
    """Run the benchmark suite (see benchmarks/run_benchmark.py)."""
    from benchmarks.run_benchmark import main as bench_main

    argv = ["--scale", *scale, "--minutes", str(minutes), "--seeds", str(seeds)]
    if out:
        argv += ["--out", str(out)]
    if workers:
        argv += ["--workers", str(workers)]
    sys.exit(bench_main(argv))


@app.command()
def world(scale: str = "small", seed: int = 42, out: Path | None = None) -> None:
    """Dump a freshly built world as JSON (used for UI fixtures)."""
    from nexus.twin import build_world, spec_for

    w = build_world(spec_for(scale, seed=seed))
    text = json.dumps(w.to_dict(), default=str)
    if out:
        out.write_text(text, encoding="utf-8")
        console.print(f"saved {len(text):,} bytes → {out}")
    else:
        console.print(text[:2000] + " …")


if __name__ == "__main__":
    app()
