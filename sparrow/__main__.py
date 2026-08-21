import argparse

import uvicorn

from sparrow.config.loader import load_config


def run_server() -> None:
    config = load_config()
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
        description="SparroW - OpenAI-compatible router for keyless free LLM providers",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser(
        "init",
        help="Fetch models from providers and update providers.toml",
    )

    args = parser.parse_args()

    if args.command == "init":
        run_init()
    else:
        run_server()


if __name__ == "__main__":
    main()
