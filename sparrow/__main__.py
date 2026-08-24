import argparse

import uvicorn

from sparrow.config.loader import load_all_providers, load_config


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

    args = parser.parse_args()

    if args.command == "init":
        run_init()
    else:
        run_server()


if __name__ == "__main__":
    main()
