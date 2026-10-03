from __future__ import annotations

import builtins
import dis
import importlib
import types

import pytest

# Importing app.main first also imports and registers every router, which is
# what makes the router modules resolvable from sys.modules below.
import app.main

ROUTER_MODULES = [
    "app.routers.admin",
    "app.routers.alarms",
    "app.routers.audit",
    "app.routers.auth",
    "app.routers.client_portal",
    "app.routers.clients",
    "app.routers.devices",
    "app.routers.firmware",
    "app.routers.gateways",
    "app.routers.hierarchy",
    "app.routers.integrations",
    "app.routers.pages",
    "app.routers.profiles",
    "app.routers.root",
    "app.routers.telemetry",
    "app.routers.webhooks",
    "app.routers.ws",
]

BUILTIN_NAMES = set(dir(builtins))


def _walk_code(code: types.CodeType):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _walk_code(const)


def _undefined_globals(module) -> set[str]:
    """
    Names the module's own bytecode loads as globals but that exist neither in
    the module namespace nor in builtins.

    Every one of these is a guaranteed NameError the moment the surrounding
    code path runs. They are invisible to import-time checks and to
    ``compileall``, which is how a previous refactor shipped 171 of them
    across the router package -- including one on the first line of the login
    handler, which broke authentication entirely.
    """
    namespace = set(vars(module))
    with open(module.__file__, encoding="utf-8") as handle:
        source = handle.read()

    undefined = set()
    for code in _walk_code(compile(source, module.__file__, "exec")):
        for instruction in dis.get_instructions(code):
            if instruction.opname not in ("LOAD_GLOBAL", "LOAD_NAME"):
                continue
            name = instruction.argval
            if name in namespace or name in BUILTIN_NAMES:
                continue
            # Class bodies legitimately load __annotations__ lazily.
            if name == "__annotations__":
                continue
            undefined.add(name)
    return undefined


@pytest.mark.parametrize("module_name", ROUTER_MODULES)
def test_router_has_no_undefined_names(module_name: str):
    module = importlib.import_module(module_name)
    undefined = _undefined_globals(module)
    assert not undefined, (
        f"{module_name} references names that are neither imported nor "
        f"defined; these raise NameError at request time: {sorted(undefined)}"
    )


def test_main_has_no_undefined_names():
    undefined = _undefined_globals(app.main)
    assert not undefined, (
        "app.main references names that are neither imported nor defined; "
        f"these raise NameError at request time: {sorted(undefined)}"
    )
