"""`register_tool`: register an MCP tool from input model, output model and handler.

Where this sits: called by each adapter's `register_tools(mcp)`; the `mcp`
object is built in `server.py`.

Key ideas:
- MCP server exposes tools with JSON schemas: the schema is derived from the
  Pydantic models, so the models are the contract and cannot drift from the
  code.
- Flat input schema: the SDK builds a tool's schema from the function
  signature, and a function taking one Pydantic model would nest everything
  under one parameter name. A synthetic flat signature gives clients the
  `{"field": value}` shape they expect while the handler still receives one
  validated model instance.
- Consistency is enforced at registration time: snake_case names and
  one-line descriptions are checked, so a malformed tool fails at startup.

Read next: `result.py`, then a concrete adapter such as `mcp_payment/app/tools.py`.
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
    catch its own failures and return `common.result.fail(...)`, because an
    exception would surface to the MCP client as an opaque transport error.
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
