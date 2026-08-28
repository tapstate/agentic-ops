from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from ao_work.output import EXIT_CAPABILITY_GAP, RuntimeErrorResult

CATALOG_RELATIVE_PATH = Path("developer/standards/capabilities/operations.yaml")
CONTRACTS_RELATIVE_PATH = Path("developer/standards/contracts/operations")
CAPABILITY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
ALLOWED_STATUS = frozenset({"implemented", "capability_gap"})
ALLOWED_VISIBILITY = frozenset({"public", "internal"})
ALLOWED_SOURCE = frozenset({"contract", "runtime"})
CHINESE_PATTERN = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True)
class Capability:
    capability_id: str
    source: str
    contract: str | None
    status: str
    visibility: str
    commands: tuple[tuple[str, ...], ...]
    summary: str
    next_action: str
    input_schema: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.capability_id,
            "source": self.source,
            "contract": self.contract,
            "status": self.status,
            "visibility": self.visibility,
            "callable": self.status == "implemented" and self.visibility == "public",
            "commands": [list(command) for command in self.commands],
            "summary": self.summary,
            "next_action": self.next_action,
        }
        if self.input_schema is not None:
            result["input_schema"] = self.input_schema
        return result


@dataclass(frozen=True)
class CapabilityCatalog:
    schema_version: str
    workplane: str
    capabilities: tuple[Capability, ...]

    @classmethod
    def load(cls, install_root: Path) -> "CapabilityCatalog":
        root = install_root.expanduser().resolve()
        path = root / CATALOG_RELATIVE_PATH
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise _catalog_error(
                "capability_catalog_not_found",
                f"能力目录不存在：{path}",
                "请重新安装 AgenticOps developer 资产",
            ) from error
        except (OSError, yaml.YAMLError) as error:
            raise _catalog_error(
                "capability_catalog_invalid",
                f"能力目录无法读取：{type(error).__name__}",
                "请停止执行操作并联系 AgenticOps 维护者修复能力目录",
            ) from error

        if not isinstance(payload, dict):
            raise _invalid_catalog("顶层必须是映射")
        if str(payload.get("schema_version", "")) != "1":
            raise _invalid_catalog("schema_version 必须为 1")
        if payload.get("workplane") != "developer":
            raise _invalid_catalog("workplane 必须为 developer")
        raw_capabilities = payload.get("capabilities")
        if not isinstance(raw_capabilities, list) or not raw_capabilities:
            raise _invalid_catalog("capabilities 必须是非空列表")

        capabilities = tuple(
            sorted(
                (_parse_capability(item) for item in raw_capabilities),
                key=lambda item: item.capability_id,
            )
        )
        _validate_uniqueness(capabilities)
        _validate_contract_coverage(root, capabilities)
        return cls(
            schema_version="1",
            workplane="developer",
            capabilities=capabilities,
        )

    def list(self, *, include_internal: bool = False) -> list[dict[str, Any]]:
        return [
            capability.to_dict()
            for capability in self.capabilities
            if include_internal or capability.visibility == "public"
        ]

    def show(self, capability_id: str) -> dict[str, Any]:
        for capability in self.capabilities:
            if capability.capability_id == capability_id:
                return capability.to_dict()
        raise _catalog_error(
            "capability_not_found",
            f"能力目录中不存在操作：{capability_id}",
            "请先运行 ao-work capability list；不要猜测或调用旧命令",
        )


def configure_capability_parser(
    subparsers: argparse._SubParsersAction[Any],
) -> None:
    parser = subparsers.add_parser("capability")
    commands = parser.add_subparsers(dest="command", required=True)
    list_parser = commands.add_parser("list")
    list_parser.add_argument("--include-internal", action="store_true")
    show_parser = commands.add_parser("show")
    show_parser.add_argument("capability_id")


def execute_capability(
    args: argparse.Namespace,
    install_root: Path,
) -> dict[str, Any]:
    catalog = CapabilityCatalog.load(install_root)
    if args.command == "list":
        capabilities = catalog.list(include_internal=args.include_internal)
        return {
            "catalog_schema_version": catalog.schema_version,
            "workplane": catalog.workplane,
            "capabilities": capabilities,
            "count": len(capabilities),
            "internal_included": bool(args.include_internal),
        }
    capability = catalog.show(args.capability_id)
    return {
        "catalog_schema_version": catalog.schema_version,
        "workplane": catalog.workplane,
        "capability_status": capability["status"],
        "callable": capability["callable"],
        "capability": capability,
    }


def _parse_capability(payload: object) -> Capability:
    if not isinstance(payload, dict):
        raise _invalid_catalog("每个 capability 必须是映射")
    allowed_keys = {
        "id",
        "source",
        "contract",
        "status",
        "visibility",
        "commands",
        "summary",
        "next_action",
        "input_schema",
    }
    unknown = sorted(set(payload) - allowed_keys)
    if unknown:
        raise _invalid_catalog(f"capability 包含未知字段：{', '.join(unknown)}")

    capability_id = _required_text(payload, "id")
    if not CAPABILITY_ID_PATTERN.fullmatch(capability_id):
        raise _invalid_catalog(f"能力编号格式无效：{capability_id}")
    source = _required_text(payload, "source")
    if source not in ALLOWED_SOURCE:
        raise _invalid_catalog(f"{capability_id} source 无效：{source}")
    status = _required_text(payload, "status")
    if status not in ALLOWED_STATUS:
        raise _invalid_catalog(f"{capability_id} status 无效：{status}")
    visibility = _required_text(payload, "visibility")
    if visibility not in ALLOWED_VISIBILITY:
        raise _invalid_catalog(f"{capability_id} visibility 无效：{visibility}")
    summary = _required_text(payload, "summary")
    next_action = _required_text(payload, "next_action")
    input_schema = payload.get("input_schema")
    if input_schema is not None and not isinstance(input_schema, dict):
        raise _invalid_catalog(f"{capability_id} input_schema 必须是对象")

    contract_value = payload.get("contract")
    contract = contract_value.strip() if isinstance(contract_value, str) else None
    if source == "contract" and not contract:
        raise _invalid_catalog(f"{capability_id} 缺少 contract")
    if source == "runtime" and contract is not None:
        raise _invalid_catalog(f"{capability_id} runtime 能力不得声明 contract")

    raw_commands = payload.get("commands")
    if not isinstance(raw_commands, list):
        raise _invalid_catalog(f"{capability_id} commands 必须是列表")
    commands: list[tuple[str, ...]] = []
    for raw_command in raw_commands:
        if not isinstance(raw_command, list) or not raw_command:
            raise _invalid_catalog(f"{capability_id} command 必须是非空字符串列表")
        if not all(isinstance(token, str) and token.strip() for token in raw_command):
            raise _invalid_catalog(f"{capability_id} command token 无效")
        commands.append(tuple(token.strip() for token in raw_command))

    if status == "implemented" and not commands:
        raise _invalid_catalog(f"{capability_id} implemented 能力必须声明命令路径")
    if status == "capability_gap":
        if commands:
            raise _invalid_catalog(f"{capability_id} capability_gap 不得声明可调用命令")
        if not CHINESE_PATTERN.search(next_action):
            raise _invalid_catalog(f"{capability_id} capability_gap 缺少中文 next_action")

    return Capability(
        capability_id=capability_id,
        source=source,
        contract=contract,
        status=status,
        visibility=visibility,
        commands=tuple(commands),
        summary=summary,
        next_action=next_action,
        input_schema=input_schema,
    )


def _validate_uniqueness(capabilities: tuple[Capability, ...]) -> None:
    ids: set[str] = set()
    contracts: set[str] = set()
    commands: set[tuple[str, ...]] = set()
    for capability in capabilities:
        if capability.capability_id in ids:
            raise _invalid_catalog(f"能力编号重复：{capability.capability_id}")
        ids.add(capability.capability_id)
        if capability.contract:
            if capability.contract in contracts:
                raise _invalid_catalog(f"契约目录项重复：{capability.contract}")
            contracts.add(capability.contract)
        for command in capability.commands:
            if command in commands:
                raise _invalid_catalog(f"命令路径重复：{' '.join(command)}")
            commands.add(command)


def _validate_contract_coverage(
    install_root: Path,
    capabilities: tuple[Capability, ...],
) -> None:
    contracts_root = (install_root / CONTRACTS_RELATIVE_PATH).resolve()
    expected = {
        f"contracts/operations/{path.name}"
        for path in contracts_root.glob("*.yaml")
        if path.is_file()
    }
    declared = {
        capability.contract
        for capability in capabilities
        if capability.contract is not None
    }
    if expected != declared:
        missing = sorted(expected - declared)
        extra = sorted(declared - expected)
        raise _invalid_catalog(
            f"契约覆盖不完整；missing={missing or []}; extra={extra or []}"
        )

    for capability in capabilities:
        if capability.contract is None:
            continue
        contract_path = (install_root / "developer" / "standards" / capability.contract).resolve()
        try:
            contract_path.relative_to(contracts_root)
        except ValueError as error:
            raise _invalid_catalog(
                f"{capability.capability_id} contract 越出 operations 目录"
            ) from error
        try:
            contract_payload = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise _invalid_catalog(
                f"{capability.capability_id} contract 无法读取：{type(error).__name__}"
            ) from error
        if not isinstance(contract_payload, dict) or contract_payload.get("operation") != capability.capability_id:
            raise _invalid_catalog(
                f"{capability.capability_id} 与契约 operation 不一致"
            )


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_catalog(f"capability 缺少字符串字段 {key}")
    return value.strip()


def _invalid_catalog(detail: str) -> RuntimeErrorResult:
    return _catalog_error(
        "capability_catalog_invalid",
        f"能力目录无效：{detail}",
        "请停止执行操作并联系 AgenticOps 维护者修复能力目录",
    )


def _catalog_error(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="capability_gap",
        exit_code=EXIT_CAPABILITY_GAP,
        retry_safe=False,
        required_human_action=action,
    )
