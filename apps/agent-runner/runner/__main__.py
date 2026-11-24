# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""Entrypoint: load config, reap stale sandboxes, start both listeners."""

import logging
import signal
import threading

from .api import SandboxAPI
from .config import Config, ConfigError
from .egress import EgressProxy
from .llm_proxy import LLMProxy
from .sandbox import SandboxRunner, reap_stale_sandboxes


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        config = Config.from_env()
    except ConfigError as exc:
        logging.error("agent runner configuration error: %s", exc)
        return 2

    reap_stale_sandboxes(config)

    proxy = LLMProxy(config)
    proxy.start()

    egress = EgressProxy(config, proxy.registry)
    egress.start()

    runner = SandboxRunner(config, proxy)
    api = SandboxAPI(config, runner)
    api.start()

    stop = threading.Event()

    def request_stop(signum, frame):
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    stop.wait()

    api.shutdown()
    proxy.stop()
    egress.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
