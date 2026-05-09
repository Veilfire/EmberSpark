"""FastAPI application factory.

The factory takes a resolved ``SparkRuntime`` config (not raw CLI args) so the
web surface is driven entirely by ``~/.spark/spark.yaml``. The caller must
have already checked ``spec.web.enabled`` — the factory raises if it's False.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from spark.config.runtime_config import (
    SparkRuntime,
    WebBindLan,
    WebBindLoopback,
    WebBindPublic,
)
from spark.logging import configure_logging, get_logger
from spark.persistence.db import init_db
from spark.runtime import get_secret_manager
from spark.web.auth import init_auth
from spark.web.credentials import GeneratedCredentials, display_banner
from spark.web.middleware import (
    CidrAllowlistMiddleware,
    SecurityHeadersMiddleware,
    SimpleRateLimitMiddleware,
)
from spark.web.api import (
    annotations as annotations_routes,
    audit,
    auth_routes,
    chat,
    cost,
    deliverables as deliverables_routes,
    filtering as filtering_routes,
    forensic as forensic_routes,
    providers as provider_routes,
    guardrails as guardrails_routes,
    memory,
    notifications as notifications_routes,
    ops,
    persona as persona_routes,
    plugin_config as plugin_config_routes,
    replay as replay_routes,
    scheduler,
    security,
    settings as settings_routes,
    skills as skills_routes,
    stats as stats_routes,
    stream,
    templates as templates_routes,
)

log = get_logger("spark.web")

STATIC_DIR = Path(__file__).parent / "static"


class WebDisabled(RuntimeError):
    pass


async def _seed_plugin_registry() -> None:
    """Populate ``plugin_registry`` from the in-memory plugin registry.

    Hashes each plugin's module source so the Ops page surfaces both
    the version *and* a fingerprint operators can compare across
    deployments. Idempotent — re-running just refreshes
    ``last_seen_at`` and tracks any module-source drift via
    ``module_hash``.
    """
    import hashlib
    import inspect

    from spark.persistence.db import session_scope
    from spark.persistence.repositories import PluginRegistryRepository
    from spark.plugins.registry import default_registry

    registry = default_registry()
    async with session_scope() as session:
        repo = PluginRegistryRepository(session)
        for name in registry.names():
            try:
                handle = registry.get(name)
                cls = handle.cls
                version = str(getattr(cls, "version", "0.0.0"))
                # Hash the plugin module's source. Survives reloads,
                # detects code edits, irrelevant to runtime behavior.
                try:
                    src = inspect.getsource(inspect.getmodule(cls) or cls)
                except (OSError, TypeError):
                    src = repr(cls)
                module_hash = hashlib.sha256(src.encode("utf-8")).hexdigest()
                await repo.record(
                    name=name, version=version, module_hash=module_hash
                )
            except Exception as exc:  # pragma: no cover — per-plugin best-effort
                log.warning(
                    "plugin_registry_seed_one_failed",
                    plugin=name,
                    error=str(exc),
                )


async def _seed_default_persona() -> None:
    """Create a starter persona on first boot if none exists."""
    from spark.persistence.db import session_scope
    from spark.persistence.learning_models import PersonaRow
    from spark.persistence.learning_repos import PersonaRepository

    async with session_scope() as session:
        repo = PersonaRepository(session)
        existing = await repo.list_all()
        if existing:
            return
        seed = PersonaRow(
            persona_id="pers-default",
            name="Default",
            description="Baseline persona. Edit in the Persona page.",
            system_prompt=(
                "You are the Spark agent. You operate under strict budgets and a "
                "plugin allowlist. Be concise, accurate, and respect the privacy "
                "boundaries the runtime enforces. Prefer structured tool calls "
                "over free-form speculation."
            ),
            tone="direct, operator-focused",
            is_active=True,
        )
        await repo.upsert(seed)


def build_app(config: SparkRuntime) -> FastAPI:
    """Build the FastAPI app from a SparkRuntime config.

    Raises WebDisabled if `spec.web.enabled` is false. The caller decides
    whether that is fatal (CLI) or a silent no-op (tests).
    """
    web_cfg = config.spec.web
    if not web_cfg.enabled:
        raise WebDisabled(
            "web UI disabled. Set spec.web.enabled=true in ~/.spark/spark.yaml."
        )

    bind = web_cfg.bind
    app = FastAPI(
        title="Spark",
        description="Spark — local-first agent runtime for bounded autonomy",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
    )

    # Background watchers — started in lifespan, stopped on shutdown.
    app.state.deliverables_watcher = None
    app.state.grants_watcher = None
    app.state.scheduler = None

    @app.on_event("startup")
    async def _startup() -> None:
        await init_db()
        secrets_mgr = get_secret_manager()
        configure_logging(tracked_secret_values=secrets_mgr.known_values)
        # Wire the secrets-vault classifier into the data-class
        # guardrail so an exact match against any tracked vault value
        # triggers the `secrets.vault` detector.
        try:
            from spark.privacy.classifiers import (  # noqa: PLC0415
                register_vault_classifier,
            )

            register_vault_classifier(secrets_mgr.known_values)
        except Exception as exc:  # pragma: no cover — classifier is best-effort
            log.warning("vault_classifier_register_failed", error=str(exc))
        await _seed_default_persona()
        try:
            await settings_routes.load_dynamic_settings()
        except Exception as exc:  # pragma: no cover — best-effort
            log.warning("session_settings_load_failed", error=str(exc))
        try:
            from spark.web.api.chat import reconcile_orphan_turns  # noqa: PLC0415

            await reconcile_orphan_turns()
        except Exception as exc:  # pragma: no cover — best-effort
            log.warning("chat_turn_reconcile_failed", error=str(exc))

        # Preload sentence-transformers embedding models for every agent
        # with long-term memory enabled. The first download is ~10s; we'd
        # rather pay it during boot than block the user's first run or
        # chat turn. Off-thread because the SentenceTransformer
        # constructor is synchronous and blocking.
        try:
            import asyncio as _asyncio  # noqa: PLC0415
            from spark.memory.embeddings import preload_all  # noqa: PLC0415

            await _asyncio.to_thread(preload_all)
        except Exception as exc:  # pragma: no cover — best-effort
            log.warning("embeddings_preload_failed", error=str(exc))

        # Seed the plugin registry table from the in-memory registry so
        # the Ops page can list every loaded plugin with its module
        # hash. Without this seed the table stays empty until the
        # plugin-hash watcher (or a tool call) writes a row, and the
        # Ops UI's "Registered plugins" count shows 0 even though all
        # plugins are actually loaded.
        try:
            await _seed_plugin_registry()
        except Exception as exc:  # pragma: no cover — best-effort
            log.warning("plugin_registry_seed_failed", error=str(exc))

        # Kick off notification producers that don't have a natural host.
        from spark.config.runtime_config import get_data_volume
        from spark.notifications.deliverables_watcher import DeliverablesWatcher
        from spark.notifications.grants_watcher import GrantsWatcher

        dv = get_data_volume()
        if dv is not None:
            try:
                watcher = DeliverablesWatcher(dv.deliverables_path)
                await watcher.start()
                app.state.deliverables_watcher = watcher
            except Exception as exc:  # pragma: no cover
                log.warning("deliverables_watcher_start_failed", error=str(exc))

        try:
            grants_watcher = GrantsWatcher()
            await grants_watcher.start()
            app.state.grants_watcher = grants_watcher
        except Exception as exc:  # pragma: no cover
            log.warning("grants_watcher_start_failed", error=str(exc))

        try:
            from spark.scheduler import set_scheduler
            from spark.scheduler.scheduler import SparkScheduler

            scheduler = SparkScheduler()
            await scheduler.start()
            set_scheduler(scheduler)
            app.state.scheduler = scheduler
        except Exception as exc:  # pragma: no cover
            log.warning("scheduler_start_failed", error=str(exc))

        log.info(
            "web.startup",
            bind_mode=bind.mode,
            host=bind.host,
            port=bind.port,
        )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if app.state.deliverables_watcher is not None:
            try:
                await app.state.deliverables_watcher.stop()
            except Exception:  # pragma: no cover
                pass
        if app.state.grants_watcher is not None:
            try:
                await app.state.grants_watcher.stop()
            except Exception:  # pragma: no cover
                pass
        if app.state.scheduler is not None:
            try:
                from spark.scheduler import set_scheduler

                await app.state.scheduler.shutdown()
                set_scheduler(None)
            except Exception:  # pragma: no cover
                pass

    # Defense in depth: middlewares applied in reverse order of `add_middleware`.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(SimpleRateLimitMiddleware, requests_per_minute=web_cfg.rate_limit_per_minute)

    allowed_cidrs: list[str] = []
    trusted_proxies: list[str] = []
    if isinstance(bind, WebBindLan):
        allowed_cidrs = bind.allowed_cidrs
        trusted_proxies = bind.trusted_proxies
    elif isinstance(bind, WebBindPublic):
        allowed_cidrs = bind.allowed_cidrs
        trusted_proxies = bind.trusted_proxies
    # Loopback mode: the kernel already rejects non-loopback source IPs for
    # a 127.0.0.1 bind, so no CIDR middleware needed.

    if allowed_cidrs:
        app.add_middleware(
            CidrAllowlistMiddleware,
            allowed_cidrs=allowed_cidrs,
            trusted_proxies=trusted_proxies,
        )

    cors_origins_env = os.environ.get("SPARK_WEB_ALLOW_ORIGIN", "")
    origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    if "*" in origins:
        raise RuntimeError(
            "SPARK_WEB_ALLOW_ORIGIN='*' is refused: wildcard origins cannot be "
            "combined with credentialed requests. Set explicit origins."
        )
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
            allow_headers=["Content-Type", "X-Spark-Token"],
            max_age=600,
        )

    # Routers
    app.include_router(auth_routes.router, prefix="/api/auth", tags=["auth"])
    app.include_router(scheduler.router, prefix="/api/scheduler", tags=["scheduler"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(security.router, prefix="/api/security", tags=["security"])
    app.include_router(filtering_routes.router, prefix="/api/filtering", tags=["filtering"])
    app.include_router(cost.router, prefix="/api/cost", tags=["cost"])
    app.include_router(memory.router, prefix="/api/memory", tags=["memory"])
    app.include_router(skills_routes.router, prefix="/api/skills", tags=["skills"])
    app.include_router(audit.router, prefix="/api/audit", tags=["audit"])
    app.include_router(ops.router, prefix="/api/ops", tags=["ops"])
    app.include_router(persona_routes.router, prefix="/api/persona", tags=["persona"])
    app.include_router(plugin_config_routes.router, prefix="/api/plugin-config", tags=["plugin-config"])
    app.include_router(replay_routes.router, prefix="/api/replay", tags=["replay"])
    app.include_router(stats_routes.router, prefix="/api/stats", tags=["stats"])
    app.include_router(guardrails_routes.router, prefix="/api/guardrails", tags=["guardrails"])
    app.include_router(annotations_routes.router, prefix="/api/annotations", tags=["annotations"])
    app.include_router(notifications_routes.router, prefix="/api/notifications", tags=["notifications"])
    app.include_router(deliverables_routes.router, prefix="/api/deliverables", tags=["deliverables"])
    app.include_router(templates_routes.router, prefix="/api/templates", tags=["templates"])
    app.include_router(forensic_routes.router, prefix="/api/forensic", tags=["forensic"])
    app.include_router(provider_routes.router, prefix="/api/providers", tags=["providers"])
    app.include_router(stream.router, prefix="/api/stream", tags=["stream"])
    app.include_router(settings_routes.router, prefix="/api/settings", tags=["settings"])

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": "0.1.0"}

    if STATIC_DIR.exists() and any(STATIC_DIR.iterdir()):
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str) -> FileResponse:
            # Root-level static files (favicon.ico, spark-icon.png,
            # apple-touch-icon.png, robots.txt, ...) live alongside
            # index.html and need to be served as themselves, not the
            # SPA shell. Anything that isn't a real file falls through
            # to index.html so client-side routes still resolve.
            if full_path and "/" not in full_path and "\\" not in full_path:
                candidate = STATIC_DIR / full_path
                if candidate.is_file():
                    return FileResponse(candidate)
            index = STATIC_DIR / "index.html"
            if not index.exists():
                return JSONResponse({"detail": "frontend not built"}, status_code=503)
            return FileResponse(index)

    return app


def build_app_with_auth(
    config: SparkRuntime,
    *,
    rotate_credentials: bool | None = None,
    rotate_token: bool = False,
) -> tuple[FastAPI, GeneratedCredentials | None]:
    """Build the app and initialize auth in one call.

    Returns (app, fresh_credentials_or_none). If fresh credentials were minted
    the caller MUST print them to stderr once and then forget them.
    """
    web_cfg = config.spec.web
    if rotate_credentials is None:
        rotate_credentials = web_cfg.credentials.rotate_on_startup

    # Cookie `Secure` flag is derived from the bind mode. Public (which
    # requires TLS) → True. LAN behind a trusted HTTPS proxy → operator opts
    # in via env var (SPARK_WEB_COOKIE_SECURE=1). Loopback → False, by design.
    cookie_secure = isinstance(web_cfg.bind, WebBindPublic)
    if os.environ.get("SPARK_WEB_COOKIE_SECURE", "").strip() in {"1", "true", "yes"}:
        cookie_secure = True

    _state, fresh = init_auth(
        credentials_path=web_cfg.credentials.path,
        session_ttl_seconds=web_cfg.session_ttl_seconds,
        rotate_credentials=rotate_credentials,
        rotate_token=rotate_token,
        cookie_secure=cookie_secure,
    )
    app = build_app(config)
    return app, fresh


def banner_for(
    config: SparkRuntime, creds: GeneratedCredentials | None
) -> str | None:
    """Render the one-shot credential banner for display to stderr."""
    if creds is None:
        return None
    from spark.config.runtime_config import WebBindPublic  # noqa: PLC0415

    bind = (config.spec.web.bind.host, config.spec.web.bind.port)
    tls = isinstance(config.spec.web.bind, WebBindPublic)
    return display_banner(creds, bind, tls=tls)
