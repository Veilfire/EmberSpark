"""In-memory plugin registry + hash tracking.

Plugins are discovered via `importlib.metadata.entry_points` on the
``spark.plugins`` group. Discovery does not imply enablement — an agent must
explicitly allowlist a plugin in its YAML before it becomes usable for that
agent.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from spark.plugins.base import ToolPlugin
from spark.utils.hashing import sha256_file


@dataclass(frozen=True)
class PluginHandle:
    name: str
    version: str
    module: str
    class_name: str
    cls: type[ToolPlugin]
    module_hash: str


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, PluginHandle] = {}

    def discover_entrypoints(self) -> None:
        """Discover plugins declared via `spark.plugins` entry points."""
        try:
            eps = metadata.entry_points(group="spark.plugins")
        except TypeError:  # pragma: no cover — older selection API
            eps = metadata.entry_points().get("spark.plugins", [])  # type: ignore[assignment]
        for ep in eps:
            try:
                cls = ep.load()
            except Exception as exc:  # pragma: no cover
                raise RuntimeError(f"failed to load plugin entry point {ep.name!r}: {exc}") from exc
            self._register(cls)

    def register_class(self, cls: type[ToolPlugin]) -> None:
        self._register(cls)

    def _register(self, cls: type[ToolPlugin]) -> None:
        _validate_plugin_class(cls)
        module = inspect.getmodule(cls)
        module_name = module.__name__ if module is not None else ""
        module_file = inspect.getfile(cls)
        module_hash = sha256_file(Path(module_file))
        handle = PluginHandle(
            name=cls.name,
            version=cls.version,
            module=module_name,
            class_name=cls.__name__,
            cls=cls,
            module_hash=module_hash,
        )
        self._plugins[cls.name] = handle

    def get(self, name: str) -> PluginHandle:
        if name not in self._plugins:
            raise KeyError(f"Unknown plugin {name!r}; did you enable it in agent.plugins.allow?")
        return self._plugins[name]

    def has(self, name: str) -> bool:
        return name in self._plugins

    def names(self) -> list[str]:
        return sorted(self._plugins.keys())


def _validate_plugin_class(cls: type[ToolPlugin]) -> None:
    required = (
        "name",
        "version",
        "description",
        "input_schema",
        "output_schema",
        "config_schema",
        "required_permissions",
        "required_secrets",
        "sensitivity",
        "filter_output_before_model",
        "needs_network",
    )
    missing = [attr for attr in required if not hasattr(cls, attr)]
    if missing:
        raise TypeError(f"Plugin {cls.__name__} missing contract fields: {missing}")
    if not hasattr(cls, "execute"):
        raise TypeError(f"Plugin {cls.__name__} missing async `execute` method")


def default_registry() -> PluginRegistry:
    """Build a registry preloaded with the built-in plugins."""
    from spark.plugins.builtins.csv_io import CsvIoPlugin
    from spark.plugins.builtins.datetime_tool import DatetimePlugin
    from spark.plugins.builtins.email_sender import EmailSenderPlugin
    from spark.plugins.builtins.filesystem import FilesystemPlugin
    from spark.plugins.builtins.git import GitPlugin
    from spark.plugins.builtins.http_client import HttpClientPlugin
    from spark.plugins.builtins.http_tool import HttpToolPlugin
    from spark.plugins.builtins.image_gen import ImageGenPlugin
    from spark.plugins.builtins.json_query import JsonQueryPlugin
    from spark.plugins.builtins.markdown_writer import MarkdownWriterPlugin
    from spark.plugins.builtins.pdf_reader import PdfReaderPlugin
    from spark.plugins.builtins.rss_reader import RssReaderPlugin
    from spark.plugins.builtins.shell import ShellPlugin
    from spark.plugins.builtins.sqlite import SqlitePlugin
    from spark.plugins.builtins.telegram_messenger import TelegramMessengerPlugin
    from spark.plugins.builtins.web_search import WebSearchPlugin
    from spark.plugins.builtins.webhook import WebhookPlugin

    reg = PluginRegistry()
    for cls in (
        # Phase F original built-ins
        FilesystemPlugin,
        HttpClientPlugin,
        MarkdownWriterPlugin,
        ShellPlugin,
        SqlitePlugin,
        # Phase G2 Tier 1
        WebSearchPlugin,
        HttpToolPlugin,
        PdfReaderPlugin,
        DatetimePlugin,
        CsvIoPlugin,
        # Phase G2 Tier 2
        EmailSenderPlugin,
        GitPlugin,
        JsonQueryPlugin,
        RssReaderPlugin,
        ImageGenPlugin,
        # External integrations — outbound webhooks + Telegram bot
        WebhookPlugin,
        TelegramMessengerPlugin,
    ):
        reg.register_class(cls)
    # Also discover any entry-point plugins installed in the environment.
    try:
        reg.discover_entrypoints()
    except Exception:  # pragma: no cover
        pass
    return reg
