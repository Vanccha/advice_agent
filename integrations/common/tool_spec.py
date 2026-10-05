"""Register an MCP tool from a Pydantic input model, a Pydantic output model
and an async handler, guaranteeing every tool on every server in this layer
looks the same: snake_case name, one-line description, a flat input schema
(not a single nested object), and a `ToolResult[OutputModel]` output schema.

Why the dynamic signature: the installed `mcp` SDK (2.x, `MCPServer`, formerly
`FastMCP`) builds a tool's input schema from `inspect.signature(fn)`. A
function whose only parameter is a Pydantic model produces a schema that
nests all fields under that one parameter name (`{"inp": {...}}`), which is
not how MCP clients expect to call a tool. Building a synthetic flat
signature from `input_model.model_fields` gives clients the flat
`{"field": value, ...}` shape while the handler still receives one validated
`input_model` instance.
"""
from __future__ import annotations

import inspect
import re
from typing import Any, Awaitable, Callable, TypeVar

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from .result import ToolResult

InputT = TypeVar("InputT", bound=BaseModel)

Handler = Callable[[InputT], Awaitable[ToolResult[Any]]]

_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


def register_tool(
    mcp: Any,
    *,
    name: str,
    description: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    handler: Handler,
) -> None:
    """Register `handler` as tool `name` on the given `MCPServer` instance.

    `handler(input_model_instance) -> ToolResult` must never raise; it should
    catch its own failures and return `common.result.fail(...)`.
    """
    if not _SNAKE_CASE.match(name):
        raise ValueError(f"tool name {name!r} must be snake_case")
    if "\n" in description:
        raise ValueError(f"tool {name!r} description must be a single line")

    envelope_type = ToolResult[output_model]  # concrete type, used for schema generation only
    wrapper = _build_flat_wrapper(input_model, handler, envelope_type)
    mcp.tool(name=name, description=description)(wrapper)


def _build_flat_wrapper(
    input_model: type[BaseModel],
    handler: Handler,
    return_annotation: Any,
) -> Callable[..., Awaitable[Any]]:
    parameters: list[inspect.Parameter] = []
    for field_name, field_info in input_model.model_fields.items():
        if field_info.default is not PydanticUndefined:
            default = field_info.default
        elif field_info.default_factory is not None:
            default = field_info.default_factory()  # type: ignore[call-arg]
        else:
            default = inspect.Parameter.empty
        parameters.append(
            inspect.Parameter(
                field_name,
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=field_info.annotation,
            )
        )
    signature = inspect.Signature(parameters, return_annotation=return_annotation)

    async def wrapper(**kwargs: Any) -> ToolResult[Any]:
        validated_input = input_model(**kwargs)
        return await handler(validated_input)

    wrapper.__signature__ = signature  # type: ignore[attr-defined]
    wrapper.__name__ = handler.__name__
    wrapper.__doc__ = handler.__doc__
    return wrapper
