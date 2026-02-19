"""Dev wrapper that sets the config path for the MCP inspector."""

import mkdocs_mcp.server as _srv

_srv._config_path_override = "/workspace/polkadot-mkdocs/mkdocs.yml"
mcp = _srv.mcp
