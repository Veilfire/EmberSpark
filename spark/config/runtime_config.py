"""`SparkRuntime` — a new top-level YAML kind for runtime-wide settings.

Scope:
- Web UI: disabled by default. When enabled, must specify a bind mode
  (`loopback` | `lan` | `public`) with mode-appropriate constraints.
- Daemonization: declares how Spark should run as a background service.
  Three modes: `naked`, `docker`, `firecracker`.

The file lives at ``~/.spark/spark.yaml`` by default. ``spark serve`` and
``spark daemon install`` both read from it; ``--config PATH`` overrides.

Design rule: **fail closed**. If the YAML is absent, or the `web` section is
absent, the web UI will not start. Operators must explicitly opt in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

API_VERSION = "spark.veilfire.dev/v1alpha1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# Web configuration
# ---------------------------------------------------------------------------


class TlsConfig(_Strict):
    cert_file: Path
    key_file: Path

    @field_validator("cert_file", "key_file")
    @classmethod
    def _must_exist(cls, v: Path) -> Path:
        expanded = v.expanduser()
        if not expanded.exists():
            raise ValueError(f"TLS file not found: {expanded}")
        return expanded


class WebBindLoopback(_Strict):
    mode: Literal["loopback"] = "loopback"
    host: str = "127.0.0.1"
    port: int = Field(default=7777, ge=1, le=65535)


class WebBindLan(_Strict):
    mode: Literal["lan"] = "lan"
    host: str = "0.0.0.0"  # noqa: S104 — explicitly LAN
    port: int = Field(default=7777, ge=1, le=65535)
    allowed_cidrs: list[str] = Field(
        min_length=1,
        description="RFC1918 CIDR blocks permitted to reach the UI. Non-matching source IPs are 403'd.",
    )
    trusted_proxies: list[str] = Field(
        default_factory=list,
        description="Trusted upstream proxies; X-Forwarded-For is only honored from these.",
    )

    @field_validator("allowed_cidrs")
    @classmethod
    def _validate_cidrs(cls, v: list[str]) -> list[str]:
        import ipaddress

        for cidr in v:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(f"invalid CIDR {cidr!r}: {exc}") from exc
        return v


class WebBindPublic(_Strict):
    mode: Literal["public"] = "public"
    host: str
    port: int = Field(default=7777, ge=1, le=65535)
    allowed_cidrs: list[str] = Field(
        default_factory=list,
        description="Optional IP allowlist on top of public bind. Empty = no IP filter.",
    )
    trusted_proxies: list[str] = Field(default_factory=list)
    tls: TlsConfig = Field(
        description="TLS is MANDATORY for public bind mode.",
    )

    @field_validator("allowed_cidrs")
    @classmethod
    def _validate_cidrs(cls, v: list[str]) -> list[str]:
        import ipaddress

        for cidr in v:
            ipaddress.ip_network(cidr, strict=False)
        return v


WebBindConfig = Annotated[
    Union[WebBindLoopback, WebBindLan, WebBindPublic],  # noqa: UP007
    Field(discriminator="mode"),
]


class WebCredentialsConfig(_Strict):
    rotate_on_startup: bool = True
    display_once: bool = True  # force one-shot console display only
    path: Path = Field(default=Path("~/.spark/web-credentials.json"))


class WebConfig(_Strict):
    enabled: bool = False  # FAIL CLOSED
    bind: WebBindConfig = Field(default_factory=WebBindLoopback)
    credentials: WebCredentialsConfig = Field(default_factory=WebCredentialsConfig)
    rate_limit_per_minute: int = Field(default=120, ge=0, le=100_000)
    session_ttl_seconds: int = Field(default=3600, ge=60, le=30 * 86_400)

    @model_validator(mode="after")
    def _public_requires_tls(self) -> WebConfig:
        if isinstance(self.bind, WebBindPublic) and self.bind.tls is None:  # type: ignore[truthy-bool]
            raise ValueError("public bind mode requires tls cert_file + key_file")
        return self


# ---------------------------------------------------------------------------
# Daemon configuration
# ---------------------------------------------------------------------------


class DaemonNakedConfig(_Strict):
    mode: Literal["naked"] = "naked"
    venv_path: Path = Field(default=Path("~/.spark/venv"))
    service_name: str = "spark"
    restart_on_failure: bool = True
    log_dir: Path = Field(default=Path("~/.spark/daemon-logs"))


class DaemonDockerConfig(_Strict):
    mode: Literal["docker"] = "docker"
    image: str = "spark-runtime:latest"
    container_name: str = "spark"
    volumes: list[str] = Field(
        default_factory=lambda: ["spark-state:/data/spark"],
        description="Docker volumes in source:dest format",
    )
    network_mode: Literal["bridge", "host"] = "bridge"
    restart: Literal["no", "always", "unless-stopped"] = "unless-stopped"
    memory_mb: int = Field(default=2048, ge=256)
    cpus: float = Field(default=2.0, gt=0)


class DaemonFirecrackerConfig(_Strict):
    mode: Literal["firecracker"] = "firecracker"
    firecracker_binary: Path = Field(default=Path("/usr/local/bin/firecracker"))
    rootfs: Path = Field(default=Path("~/.spark/firecracker/rootfs.ext4"))
    kernel: Path = Field(default=Path("~/.spark/firecracker/vmlinux"))
    data_image: Path = Field(
        default=Path("~/.spark/firecracker/data.ext4"),
        description="ext4 image attached as /dev/vdb inside the guest; holds the data volume",
    )
    data_image_size_mib: int = Field(
        default=5_120,
        ge=128,
        le=1_048_576,
        description="Size in MiB of the data.ext4 image created by build-rootfs.sh",
    )
    vsock_cid: int = Field(default=3, ge=3, le=4_294_967_294)
    vcpus: int = Field(default=2, ge=1, le=32)
    memory_mib: int = Field(default=1024, ge=128, le=65_536)
    tap_device: str = "spark-tap0"
    host_cidr: str = "192.168.241.1/30"
    guest_ip: str = "192.168.241.2"
    forwarded_ports: dict[int, int] = Field(
        default_factory=lambda: {7777: 7777},
        description="host_port -> guest_port",
    )


DaemonConfig = Annotated[
    Union[DaemonNakedConfig, DaemonDockerConfig, DaemonFirecrackerConfig],  # noqa: UP007
    Field(discriminator="mode"),
]


# ---------------------------------------------------------------------------
# Secrets configuration (H1.3)
# ---------------------------------------------------------------------------


class AgeFileVaultConfig(_Strict):
    """Settings for the primary (only) secrets backend.

    The age-encrypted vault lives at ``vault_path``. Its identity key
    lives at ``identity_path`` (mode 0600) or at ``{identity_path}.age``
    when passphrase-wrapped. When ``passphrase_env`` is set, the daemon
    reads the passphrase from that environment variable at startup;
    leave it null for interactive prompting (or for unwrapped identities).
    """

    vault_path: Path = Field(default=Path("~/.spark/secrets.age"))
    identity_path: Path = Field(default=Path("~/.spark/age_identity.key"))
    passphrase_env: str | None = Field(default=None, max_length=128)
    auto_init: bool = Field(
        default=True,
        description=(
            "Auto-create the vault + identity on first boot if missing. "
            "Set False in CI or for deployments that pre-provision the "
            "vault via some external mechanism."
        ),
    )


class EnvFallbackConfig(_Strict):
    """Optional env-var fallback for unresolved secrets."""

    enabled: bool = True
    warn_on_hit: bool = True


class SecretsConfig(_Strict):
    """Runtime-wide secrets story.

    Exactly two providers: the age vault (primary) and the env fallback
    (optional). The operator configures them here and the secret manager
    is wired up at bootstrap time.
    """

    age_file: AgeFileVaultConfig = Field(default_factory=AgeFileVaultConfig)
    env_fallback: EnvFallbackConfig = Field(default_factory=EnvFallbackConfig)


# ---------------------------------------------------------------------------
# Data volume (Chroma + scratch + deliverables)
# ---------------------------------------------------------------------------


class DataVolumeConfig(_Strict):
    """Persistent user-data volume.

    One root directory, three operator-allowlisted subdirectories:

    - ``chroma_subdir`` — the vector store. **Never** bind-mounted into a
      plugin's sandbox; it is the runtime's private territory.
    - ``scratch_subdir`` — read/write temp space for plugins that need to
      stage intermediate blobs.
    - ``deliverables_subdir`` — read/write output directory. Files here are
      surfaced in the web UI's Downloads page and trigger a notification
      when a new file lands.

    When ``sqlite_on_volume=True`` (default), ``spark.db`` also moves onto
    the volume so the full runtime state survives container restarts.

    When ``enabled=False`` the runtime falls back to the legacy
    ``~/.spark/chroma`` + ``~/.spark/spark.db`` layout — backward compatible
    for ``naked`` deployments.
    """

    enabled: bool = True
    root: Path = Field(default=Path("~/.spark/data"))
    chroma_subdir: str = Field(default="chroma", min_length=1, max_length=64)
    scratch_subdir: str = Field(default="scratch", min_length=1, max_length=64)
    deliverables_subdir: str = Field(
        default="deliverables", min_length=1, max_length=64
    )
    sqlite_on_volume: bool = True
    sqlite_filename: str = Field(default="spark.db", min_length=1, max_length=64)

    @field_validator("chroma_subdir", "scratch_subdir", "deliverables_subdir", "sqlite_filename")
    @classmethod
    def _no_slashes(cls, v: str) -> str:
        if "/" in v or "\\" in v or v.startswith(".") or v in {".", ".."}:
            raise ValueError("subdirectory name must be a single path segment without slashes or leading dot")
        return v

    @model_validator(mode="after")
    def _subdirs_distinct(self) -> DataVolumeConfig:
        names = {self.chroma_subdir, self.scratch_subdir, self.deliverables_subdir}
        if len(names) != 3:
            raise ValueError(
                "chroma_subdir, scratch_subdir, and deliverables_subdir must all differ"
            )
        return self

    @property
    def root_path(self) -> Path:
        return Path(self.root).expanduser().resolve()

    @property
    def chroma_path(self) -> Path:
        return self.root_path / self.chroma_subdir

    @property
    def scratch_path(self) -> Path:
        return self.root_path / self.scratch_subdir

    @property
    def deliverables_path(self) -> Path:
        return self.root_path / self.deliverables_subdir

    @property
    def sqlite_path(self) -> Path | None:
        return self.root_path / self.sqlite_filename if self.sqlite_on_volume else None


# ---------------------------------------------------------------------------
# Memory pruning (H1.2)
# ---------------------------------------------------------------------------


class MemoryPruningRollover(_Strict):
    """Per-retention-class rollover windows, in days.

    ``None`` means "never prune this class". The defaults mirror the
    retention-class semantics used elsewhere in the codebase:

    - ``temporary`` — short-lived chat context, 7 days
    - ``expiring`` — recent but not long-term, 30 days
    - ``review`` — medium-term, 180 days
    - ``persistent`` — never pruned
    """

    temporary: int | None = Field(default=7, ge=1, le=36_500)
    expiring: int | None = Field(default=30, ge=1, le=36_500)
    review: int | None = Field(default=180, ge=1, le=36_500)
    persistent: int | None = Field(default=None, ge=1, le=36_500)


class MemoryPruningConfig(_Strict):
    """Scheduled pruning for long-term memory.

    Runs as an APScheduler cron job. When ``enabled=False`` the
    scheduler never registers the job; the old ``prune_expired`` legacy
    path is left untouched.
    """

    enabled: bool = True
    schedule: str = Field(
        default="0 3 * * *",
        description="Cron expression (5 fields) for the pruning sweep; local time",
    )
    rollover_windows: MemoryPruningRollover = Field(
        default_factory=MemoryPruningRollover
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "When True the sweep computes counts but deletes nothing. Useful "
            "for verifying a schedule change before committing to it."
        ),
    )
    notify_on_prune: bool = Field(
        default=True,
        description="Fire a MEMORY_PRUNED notification whenever the sweep deletes rows.",
    )

    @field_validator("schedule")
    @classmethod
    def _cron_shape(cls, v: str) -> str:
        parts = v.split()
        if len(parts) != 5:
            raise ValueError(
                f"memory_pruning.schedule must be a 5-field cron expression, got {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# Root: SparkRuntime
# ---------------------------------------------------------------------------


class SparkRuntimeMetadata(_Strict):
    name: str = Field(default="default", min_length=1, max_length=128)


class SparkRuntimeSpec(_Strict):
    web: WebConfig = Field(default_factory=WebConfig)
    daemon: DaemonConfig | None = None
    data_volume: DataVolumeConfig = Field(default_factory=DataVolumeConfig)
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)
    memory_pruning: MemoryPruningConfig = Field(default_factory=MemoryPruningConfig)


class SparkRuntime(_Strict):
    apiVersion: Literal["spark.veilfire.dev/v1alpha1"] = API_VERSION  # noqa: N815
    kind: Literal["SparkRuntime"] = "SparkRuntime"
    metadata: SparkRuntimeMetadata = Field(default_factory=SparkRuntimeMetadata)
    spec: SparkRuntimeSpec = Field(default_factory=SparkRuntimeSpec)

    @classmethod
    def default(cls) -> SparkRuntime:
        return cls()


DEFAULT_CONFIG_PATH = Path("~/.spark/spark.yaml").expanduser()


def load_runtime(path: Path | None = None) -> SparkRuntime:
    """Load a SparkRuntime YAML, or return a default (web disabled)."""
    p = (path or DEFAULT_CONFIG_PATH).expanduser()
    if not p.exists():
        return SparkRuntime.default()

    from ruamel.yaml import YAML

    yaml = YAML(typ="safe")
    with p.open("rb") as f:
        data = yaml.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{p}: top-level must be a mapping")
    kind = data.get("kind")
    if kind != "SparkRuntime":
        raise ValueError(f"{p}: expected kind=SparkRuntime, got {kind!r}")
    return SparkRuntime.model_validate(data)


def effective_bind(config: SparkRuntime) -> tuple[str, int]:
    b = config.spec.web.bind
    return (b.host, b.port)


def dump_example() -> str:
    """Return an example SparkRuntime YAML (used by `spark config init`)."""
    return (
        "# ~/.spark/spark.yaml — Spark runtime configuration\n"
        "apiVersion: spark.veilfire.dev/v1alpha1\n"
        "kind: SparkRuntime\n"
        "metadata:\n"
        "  name: default\n"
        "spec:\n"
        "  web:\n"
        "    enabled: false            # flip to true to enable the web UI\n"
        "    bind:\n"
        "      mode: loopback          # loopback | lan | public\n"
        "      host: 127.0.0.1\n"
        "      port: 7777\n"
        "    credentials:\n"
        "      rotate_on_startup: true\n"
        "    session_ttl_seconds: 28800\n"
        "    rate_limit_per_minute: 120\n"
        "  data_volume:\n"
        "    enabled: true             # persistent Chroma + scratch + deliverables\n"
        "    root: ~/.spark/data       # host path (naked), mount point (docker/firecracker)\n"
        "    chroma_subdir: chroma         # runtime-private vector store\n"
        "    scratch_subdir: scratch       # plugin-writable temp space\n"
        "    deliverables_subdir: deliverables  # user downloads + notification bell\n"
        "    sqlite_on_volume: true    # move spark.db onto the volume\n"
        "  # daemon: { mode: naked }   # or mode: docker, mode: firecracker\n"
    )


def write_example(path: Path | None = None) -> Path:
    target = (path or DEFAULT_CONFIG_PATH).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(dump_example())
        target.chmod(0o600)
    return target


# ---------------------------------------------------------------------------
# Process-scoped data volume accessor
# ---------------------------------------------------------------------------


_data_volume: DataVolumeConfig | None = None


def set_data_volume(cfg: DataVolumeConfig | None) -> None:
    """Record the resolved data volume for the current process.

    Called once at startup by the bootstrap code after ``load_runtime``. Any
    later subsystem (sandbox policy builder, tool runtime, deliverables
    watcher) reads it via :func:`get_data_volume`.
    """
    global _data_volume
    _data_volume = cfg


def get_data_volume() -> DataVolumeConfig | None:
    """Return the active data volume, or ``None`` if disabled/unset."""
    if _data_volume is None or not _data_volume.enabled:
        return None
    return _data_volume


def ensure_data_volume_dirs(cfg: DataVolumeConfig) -> None:
    """Create the data volume tree on disk.

    Safe to call repeatedly. Creates ``root``, ``chroma_path``,
    ``scratch_path``, and ``deliverables_path`` with mode 0700.
    """
    for p in (cfg.root_path, cfg.chroma_path, cfg.scratch_path, cfg.deliverables_path):
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
