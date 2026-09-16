from watchtower.app import run
from watchtower.config import load_config


def main() -> None:
    run(load_config())


if __name__ == "__main__":
    main()
