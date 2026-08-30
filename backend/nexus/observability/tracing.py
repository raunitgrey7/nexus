"""OpenTelemetry tracing (opt-in via NEXUS_OTEL_ENABLED)."""

from __future__ import annotations

from typing import Any

from nexus.core.config import settings
from nexus.core.logging import get_logger

log = get_logger("nexus.otel")


def setup_tracing(app: Any) -> bool:
    if not settings.otel_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

        provider = TracerProvider(resource=Resource.create({"service.name": "nexus-backend"}))
        exporter: Any
        if settings.otel_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

                exporter = OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True)
            except ImportError:
                log.warning("otel.otlp_exporter_missing", hint="pip install opentelemetry-exporter-otlp")
                exporter = ConsoleSpanExporter()
        else:
            exporter = ConsoleSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        log.info("otel.enabled", endpoint=settings.otel_endpoint or "console")
        return True
    except Exception as exc:
        log.warning("otel.setup_failed", error=str(exc)[:200])
        return False


def tracer(name: str = "nexus") -> Any:
    from opentelemetry import trace

    return trace.get_tracer(name)
