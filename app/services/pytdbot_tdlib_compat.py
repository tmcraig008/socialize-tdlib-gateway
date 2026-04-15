"""
pytdbot ships TDLib JSON typings for a specific lib version. When the runtime
tdjson binary is newer (e.g. 1.8.63) than the typings (e.g.0.9.10 → 1.8.61),
TDLib can emit @type values pytdbot does not define, which crashes dict_to_obj.

We wrap dict_to_obj and substitute a minimal object so the client keeps running.
Unknown updates are ignored by Client._handle_update (no registered handlers).
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_installed = False


class _FallbackTlObject:
    __slots__ = ("_raw", "_client")

    def __init__(self, raw: dict, client: Any = None) -> None:
        self._raw = raw
        self._client = client

    def getType(self) -> str:
        return str(self._raw.get("@type", ""))

    def __bool__(self) -> bool:
        return True


def install_pytdbot_schema_fallback() -> None:
    global _installed
    if _installed:
        return
    from pytdbot import types as td_types
    from pytdbot.utils import obj_encoder, to_camel_case

    _orig = obj_encoder.dict_to_obj

    def dict_to_obj(dict_obj: Any, client=None):
        if isinstance(dict_obj, dict) and "@type" in dict_obj:
            camel = to_camel_case(dict_obj["@type"])
            if not hasattr(td_types, camel):
                log.debug(
                    "TDLib object type %r has no pytdbot class %s; using fallback",
                    dict_obj["@type"],
                    camel,
                )
                o = _FallbackTlObject(dict_obj, client)
                return o
        return _orig(dict_obj, client)

    obj_encoder.dict_to_obj = dict_to_obj
    _installed = True
