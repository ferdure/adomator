"""CLI entry point for adomator."""

from __future__ import annotations

import sys

import click

from adomator.client import AzureDevOpsClient
from adomator.config import load_config
from adomator.reconciler import Reconciler


@click.group()
@click.version_option(package_name="adomator")
def main() -> None:
    """adomator – declarative Azure DevOps repository management.

    Manage your Azure DevOps repository settings, branch policies, and
    security permissions as code using YAML configuration files.
    """


@main.command()
@click.argument("config_file", metavar="CONFIG_FILE")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed change descriptions.")
def plan(config_file: str, verbose: bool) -> None:
    """Show changes required to reach the desired state.

    CONFIG_FILE is the path to a project YAML configuration file.

    No changes are made to Azure DevOps during a plan run.
    """
    try:
        config = load_config(config_file)
        client = AzureDevOpsClient(config.organization, config.token)
        reconciler = Reconciler(client, config)
        changes = reconciler.plan()
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Error loading configuration: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Error computing plan: {exc}", err=True)
        sys.exit(1)

    if not changes:
        click.echo("No changes required. Infrastructure is up-to-date.")
        return

    click.echo(f"Plan: {len(changes)} change(s) to apply\n")
    for change in changes:
        click.echo(f"  {change}")
        if verbose:
            for key, val in change.details.items():
                click.echo(f"      {key}: {val}")


@main.command()
@click.argument("config_file", metavar="CONFIG_FILE")
@click.option("--auto-approve", is_flag=True, help="Skip confirmation prompt.")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed change descriptions.")
def apply(config_file: str, auto_approve: bool, verbose: bool) -> None:
    """Apply changes to reach the desired state.

    CONFIG_FILE is the path to a project YAML configuration file.

    Computes a plan and then applies each change in order.  Use --auto-approve
    to skip the interactive confirmation prompt.
    """
    try:
        config = load_config(config_file)
        client = AzureDevOpsClient(config.organization, config.token)
        reconciler = Reconciler(client, config)
        changes = reconciler.plan()
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Error loading configuration: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Error computing plan: {exc}", err=True)
        sys.exit(1)

    if not changes:
        click.echo("No changes required. Infrastructure is up-to-date.")
        return

    click.echo(f"Plan: {len(changes)} change(s) to apply\n")
    for change in changes:
        click.echo(f"  {change}")
        if verbose:
            for key, val in change.details.items():
                click.echo(f"      {key}: {val}")

    if not auto_approve:
        click.confirm("\nDo you want to apply these changes?", abort=True)

    click.echo("\nApplying changes...")
    try:
        reconciler.apply(changes)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Error applying changes: {exc}", err=True)
        sys.exit(1)

    click.echo(f"\nApplied {len(changes)} change(s) successfully.")
