"""Admin commands: python -m routewise.cli create-tenant NAME"""

from __future__ import annotations

import argparse
import sys

from .auth import create_tenant
from .config import load_settings
from .db import DuplicateTenantError, Repository


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="SQLite file (default: ROUTEWISE_DB_PATH or routewise.db)")
    parser = argparse.ArgumentParser(prog="python -m routewise.cli", description="RouteWise admin")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "create-tenant", parents=[common], help="create a tenant and print its API key once"
    )
    create.add_argument("name")
    commands.add_parser("list-tenants", parents=[common], help="list tenants")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Repository(args.db or load_settings().db_path)
    repo.init_schema()

    if args.command == "list-tenants":
        tenants = repo.list_tenants()
        if not tenants:
            print("No tenants.")
        for tenant in tenants:
            print(f"{tenant.name}  {tenant.id}  {tenant.created_at}")
        return 0

    try:
        tenant, api_key = create_tenant(repo, args.name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except DuplicateTenantError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Tenant: {tenant.name} ({tenant.id})")
    print(f"API key: {api_key}")
    print("Save this key now. It is stored only as a hash and cannot be shown again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
