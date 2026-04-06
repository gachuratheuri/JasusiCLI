"""JSON schema types for tool definitions — mirrors Rust tools crate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolParameter:
    name: str
    type: str           # "string" | "integer" | "boolean" | "object" | "array"
    description: str
    required: bool = True
    enum_values: list[str] = field(default_factory=list)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: list[ToolParameter]

    def to_json_schema(self) -> dict[str, Any]:
        props: dict[str, Any] = {}
        required: list[str] = []
        for p in self.parameters:
            prop: dict[str, Any] = {
                "type": p.type,
                "description": p.description,
            }
            if p.enum_values:
                prop["enum"] = p.enum_values
            props[p.name] = prop
            if p.required:
                required.append(p.name)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        }
