"""可选 OTLP HTTP 导出；不采集请求正文、凭据、查询参数或异常文本。"""
import atexit
import functools
import inspect
import logging
import threading
import time
import uuid

from anp_business.shared.config import configuration

_lock = threading.Lock()
_configured = False
_state = None


class Telemetry:
    def __init__(self, options):
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        import os
        endpoint = options["endpoint"].rstrip("/")
        resource = Resource.create({"service.name": options["service_name"], "service.namespace": "anp",
                                    "service.version": os.getenv("ANP_SERVICE_VERSION", "0.1.0"),
                                    "service.instance.id": str(uuid.uuid4())})
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            provider = TracerProvider(resource=resource)
            trace.set_tracer_provider(provider)
        # 已有 SDK Provider 时复用；本模块只注册一次自己的导出器。
        self.spans = BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=5))
        provider.add_span_processor(self.spans)
        self.tracer = provider.get_tracer("anp.business")
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint + "/v1/metrics", timeout=5), export_interval_millis=10000)
        self.meters = MeterProvider(resource=resource, metric_readers=[reader])
        meter = self.meters.get_meter("anp.business")
        self.requests = meter.create_counter("anp.http.server.requests", unit="{request}")
        self.duration = meter.create_histogram("anp.http.server.duration", unit="s")
        self.logs = LoggerProvider(resource=resource)
        self.logs.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint + "/v1/logs", timeout=5)))
        # 仅导出本模块固定格式的事件，避免转发上游含 token/SQL 的日志。
        self.logger = logging.getLogger("anp.telemetry")
        self.logger.setLevel(logging.INFO)
        self.handler = LoggingHandler(level=logging.INFO, logger_provider=self.logs)
        self.logger.addHandler(self.handler)
        self.logger.propagate = False
        atexit.register(self.shutdown)

    def shutdown(self):
        self.logger.removeHandler(self.handler)
        self.spans.force_flush(timeout_millis=5000)
        self.spans.shutdown()
        self.meters.shutdown(timeout_millis=5000)
        self.logs.shutdown()


def telemetry():
    global _configured, _state
    with _lock:
        if not _configured:
            options = configuration()["observability"]
            if options["enabled"]:
                _state = Telemetry(options)
            _configured = True
    return _state


def traced(name):
    """为业务节点和工具加 span；保留原函数签名与 LangChain 工具元数据。"""
    def decorate(function):
        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def asynchronous(*args, **kwargs):
                state = telemetry()
                if state is None:
                    return await function(*args, **kwargs)
                with state.tracer.start_as_current_span(name, record_exception=False, set_status_on_exception=False) as span:
                    try:
                        result = await function(*args, **kwargs)
                        state.logger.info("%s.complete", name)
                        return result
                    except Exception:
                        from opentelemetry.trace import Status, StatusCode
                        span.set_status(Status(StatusCode.ERROR))
                        state.logger.error("%s.failed", name)
                        raise
            return asynchronous
        @functools.wraps(function)
        def synchronous(*args, **kwargs):
            state = telemetry()
            if state is None:
                return function(*args, **kwargs)
            with state.tracer.start_as_current_span(name, record_exception=False, set_status_on_exception=False) as span:
                try:
                    result = function(*args, **kwargs)
                    state.logger.info("%s.complete", name)
                    return result
                except Exception:
                    from opentelemetry.trace import Status, StatusCode
                    span.set_status(Status(StatusCode.ERROR))
                    state.logger.error("%s.failed", name)
                    raise
        return synchronous
    return decorate


class TelemetryMiddleware:
    """由 Agent Server 合并到其现有 ASGI 栈，覆盖业务扩展及内置 HTTP 路由。"""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "").startswith(("/api/health", "/grafana")):
            return await self.app(scope, receive, send)
        state = telemetry()
        if state is None:
            return await self.app(scope, receive, send)
        from opentelemetry import propagate, trace
        from opentelemetry.trace import Status, StatusCode
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])
                   if key.lower() in (b"traceparent", b"tracestate")}
        context = propagate.extract(headers)
        method, status, beginning = scope["method"], 500, time.monotonic()
        with state.tracer.start_as_current_span("HTTP " + method, context=context, kind=trace.SpanKind.SERVER,
                                               record_exception=False, set_status_on_exception=False) as span:
            async def record_send(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                await send(message)
            try:
                return await self.app(scope, receive, record_send)
            finally:
                route = getattr(scope.get("route"), "path", "unmatched")
                attributes = {"http.request.method": method, "http.response.status_code": status, "http.route": route}
                span.update_name(method + " " + route)
                span.set_attributes(attributes)
                if status >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                state.requests.add(1, attributes)
                state.duration.record(time.monotonic() - beginning, attributes)
                state.logger.info("http.request.complete", extra=attributes)


def http_client(**options):
    """仅包装本项目 HTTP 调用，传播 traceparent，不记录 URL/查询串和请求正文。"""
    import httpx
    class Client(httpx.AsyncClient):
        async def send(self, request, **kwargs):
            state = telemetry()
            if state is None:
                return await super().send(request, **kwargs)
            from opentelemetry import propagate, trace
            from opentelemetry.trace import Status, StatusCode
            with state.tracer.start_as_current_span("HTTP " + request.method, kind=trace.SpanKind.CLIENT,
                                                   record_exception=False, set_status_on_exception=False) as span:
                span.set_attribute("server.address", request.url.host)
                span.set_attribute("http.request.method", request.method)
                propagate.inject(request.headers)
                try:
                    result = await super().send(request, **kwargs)
                    span.set_attribute("http.response.status_code", result.status_code)
                    if result.status_code >= 500:
                        span.set_status(Status(StatusCode.ERROR))
                    return result
                except Exception:
                    span.set_status(Status(StatusCode.ERROR))
                    raise
    return Client(**options)
