from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SEMVER_PATTERN = (
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$"
)
FLOW_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,254}$"
CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
INPUT_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"


class ManifestInputField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=INPUT_NAME_PATTERN)
    type: Literal[
        "string",
        "integer",
        "number",
        "boolean",
        "array",
        "object",
    ]
    required: bool = False
    description: str | None = Field(default=None, max_length=1000)


class FlowManifest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    rpa_flow_id: str = Field(alias="rpaFlowId", pattern=FLOW_ID_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    version: str = Field(pattern=SEMVER_PATTERN, max_length=64)
    engine_type: Literal["PLAYWRIGHT_CDP"] = Field(alias="engineType")
    entrypoint: Literal["flow.py:run"]
    supported_workflow_codes: list[str] = Field(
        alias="supportedWorkflowCodes",
        min_length=1,
    )
    supported_portal_types: list[str] = Field(
        default_factory=list,
        alias="supportedPortalTypes",
    )
    input_schema: list[ManifestInputField] = Field(
        default_factory=list,
        alias="inputSchema",
    )
    capabilities: list[str] = Field(default_factory=list)
    minimum_engine_version: str | None = Field(
        default=None,
        alias="minimumEngineVersion",
        pattern=SEMVER_PATTERN,
        max_length=64,
    )

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator(
        "supported_workflow_codes",
        "supported_portal_types",
        "capabilities",
    )
    @classmethod
    def string_lists_must_be_unique_and_valid(
        cls,
        value: list[str],
    ) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = item.strip()
            if not candidate:
                raise ValueError("list values must not be blank")
            if len(candidate) > 128:
                raise ValueError("list values must not exceed 128 characters")
            if candidate in seen:
                raise ValueError("list values must be unique")
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @field_validator("supported_workflow_codes")
    @classmethod
    def workflow_codes_must_match_contract(
        cls,
        value: list[str],
    ) -> list[str]:
        import re

        if any(re.fullmatch(CODE_PATTERN, item) is None for item in value):
            raise ValueError("workflow codes contain unsupported characters")
        return value

    @field_validator("input_schema")
    @classmethod
    def input_names_must_be_unique(
        cls,
        value: list[ManifestInputField],
    ) -> list[ManifestInputField]:
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("inputSchema names must be unique")
        return value
