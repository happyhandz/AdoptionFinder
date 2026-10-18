import sys

import uvicorn

from . import auth, config


def main():
    if config.HOST not in auth.LOOPBACK and config.REQUIRE_TOKEN and len(config.API_TOKEN) < auth.MIN_TOKEN_LEN:
        sys.exit(
            f"Refusing to listen on {config.HOST}: API_TOKEN in .env is missing or shorter than "
            f"{auth.MIN_TOKEN_LEN} characters, so anyone on the network could open the app.\n"
            "Run:  .venv\\Scripts\\python -m app.phone_setup --token   (or without --token for home-network mode)"
        )
    if config.HOST not in auth.LOOPBACK and not config.REQUIRE_TOKEN:
        print(f"Phone access ON (home-network mode): listening on {config.HOST}:{config.PORT}. Devices on your private "
              "network need no token; public internet addresses still do. Install link: "
              f"http://localhost:{config.PORT}/pair")
    elif config.HOST not in auth.LOOPBACK:
        print(f"Phone access ON: listening on {config.HOST}:{config.PORT}; non-local requests need the token. "
              f"Pairing info: http://localhost:{config.PORT}/pair")
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
