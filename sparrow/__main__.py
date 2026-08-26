import argparse

import uvicorn

from sparrow.config.loader import load_all_providers, load_config
from sparrow.config.models import Settings
from sparrow.models.config import ProvidersRuntime


def run_server() -> None:
    config = load_config()
    load_all_providers()
    uvicorn.run(
        "sparrow.app:create_app",
        host=config.host,
        port=config.port,
        factory=True,
    )


def run_init() -> None:
    from sparrow.init import main as init_main

    init_main()


def _load_runtime() -> tuple[Settings, ProvidersRuntime]:
    config = load_config()
    runtime = load_all_providers()
    return config, runtime


def run_status() -> None:
    try:
        config, runtime = _load_runtime()
    except Exception as error:
        print(f"error: {error}")
        raise SystemExit(1) from None

    providers = runtime["providers"]
    model_count = sum(len(provider["models"]) for provider in providers.values())

    print("SparroW status")
    print(f"  host:        {config.host}")
    print(f"  port:        {config.port}")
    print(f"  routing:     {config.routing}")
    print(f"  providers:   {len(providers)}")
    print(f"  models:      {model_count}")


def run_config() -> None:
    try:
        load_config()
        runtime = load_all_providers()
    except Exception as error:
        print(f"error: {error}")
        raise SystemExit(1) from None

    from sparrow.config.loader import PROJECT_ROOT

    config_path = PROJECT_ROOT / "providers.json"

    print("SparroW configuration")
    print(f"  config_file: {config_path}")
    print(f"  exists:      {config_path.exists()}")
    print(f"  providers:   {len(runtime['providers'])}")
    print(f"  aliases:     {len(runtime['aliases'])}")
    print("  valid:       true")


def run_providers() -> None:
    try:
        runtime = load_all_providers()
    except Exception as error:
        print(f"error: {error}")
        raise SystemExit(1) from None

    providers = runtime["providers"]
    print(f"SparroW providers ({len(providers)})")
    for provider_id, provider in providers.items():
        models = provider["models"]
        enabled = sum(1 for model in models if model["enabled"])
        print(f"  {provider_id}")
        print(f"    name:     {provider['name']}")
        print(f"    adapter:  {provider['adapter']}")
        print(f"    auth:     {provider['auth']}")
        print(f"    models:   {len(models)} ({enabled} enabled)")
        print(f"    quota:    {provider['daily_quota'] if provider['daily_quota'] is not None else 'unlimited'}")


def run_routes() -> None:
    try:
        runtime = load_all_providers()
    except Exception as error:
        print(f"error: {error}")
        raise SystemExit(1) from None

    providers = runtime["providers"]
    aliases = runtime["aliases"]
    model_groups = runtime["model_groups"]

    print("SparroW routes")
    for provider_id, provider in providers.items():
        print(f"  {provider_id} ({provider['name']})")
        for model in provider["models"]:
            flag = "enabled" if model["enabled"] else "disabled"
            print(f"    {model['id']} [{flag}]")

    print("  aliases")
    for alias, target in aliases.items():
        print(f"    {alias} -> {target}")

    print("  model groups")
    for group_name, members in model_groups.items():
        print(f"    {group_name}: {', '.join(members)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sparrow",
        description=(
            "SparroW - OpenAI-compatible router for keyless free LLM providers. "
            "Startup requires a valid JSON provider configuration before readiness; "
            "run 'sparrow init' explicitly to refresh providers.json and models.json."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    init_parser = subparsers.add_parser(
        "init",
        help="Explicitly refresh provider models in the configured JSON files before readiness",
        description=(
            "Fetch provider models and atomically update providers.json and models.json. "
            "Use this explicit init command before starting the server when provider readiness data must be refreshed."
        ),
    )
    init_parser.set_defaults(command="init")

    status_parser = subparsers.add_parser(
        "status",
        help="Print server configuration, provider count, and model count",
        description="Load the configuration and print server status without starting the server.",
    )
    status_parser.set_defaults(command="status")

    config_parser = subparsers.add_parser(
        "config",
        help="Show the resolved config file path and validate it",
        description="Load and validate the provider configuration, printing the resolved config file path.",
    )
    config_parser.set_defaults(command="config")

    providers_parser = subparsers.add_parser(
        "providers",
        help="List all providers with model counts and enabled status",
        description="Load the provider configuration and list every provider with its model counts.",
    )
    providers_parser.set_defaults(command="providers")

    routes_parser = subparsers.add_parser(
        "routes",
        help="Show all registered routes from providers.json and models.json",
        description="Load the provider configuration and print providers, aliases, and model groups.",
    )
    routes_parser.set_defaults(command="routes")

    args = parser.parse_args()

    if args.command == "init":
        run_init()
    elif args.command == "status":
        run_status()
    elif args.command == "config":
        run_config()
    elif args.command == "providers":
        run_providers()
    elif args.command == "routes":
        run_routes()
    else:
        run_server()


if __name__ == "__main__":
    main()
