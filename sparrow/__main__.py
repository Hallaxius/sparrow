import uvicorn

from sparrow.config.loader import load_config


def main() -> None:
    config = load_config()
    uvicorn.run(
        "sparrow.app:create_app",
        host=config.host,
        port=config.port,
        factory=True,
    )


if __name__ == "__main__":
    main()
