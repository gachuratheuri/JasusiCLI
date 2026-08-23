"""JasusiCLI package.

The version resolves from installed distribution metadata (declared in
``pyproject.toml``) rather than being hardcoded here. Duplicating it produced
mutually inconsistent version strings across the package, the web adapter, the
CLI status output, and the documentation.
"""

from jasusi_cli.config.registry import VERSION as __version__

__all__ = ["__version__"]
