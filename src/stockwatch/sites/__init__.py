"""Site adapters. Every module in this package is imported, so a new site only needs a new module
containing a `@register`-decorated SiteAdapter subclass."""

import importlib
import pkgutil

from stockwatch.sites.base import SiteAdapter, registered_adapters

_INFRA = {"base", "http"}


def load_adapters() -> dict[str, SiteAdapter]:
    for module in pkgutil.iter_modules(__path__):
        if module.name not in _INFRA and not module.name.startswith("_"):
            importlib.import_module(f"{__name__}.{module.name}")
    return registered_adapters()
