"""`sufficia-api` console entry point: serves the API with uvicorn."""

from __future__ import annotations

import argparse


def serve(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(prog="sufficia-api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    # one worker: the LOCAL_EVM chain, signer queues and rate limiter are in-process state
    uvicorn.run("sufficia.api.app:app", host=args.host, port=args.port, reload=args.reload, workers=1, log_config=None)


if __name__ == "__main__":  # pragma: no cover
    serve()
