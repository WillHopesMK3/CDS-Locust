import csv
import itertools
import time
from collections import defaultdict
from datetime import datetime

from locust import events
from locust.runners import MasterRunner, WorkerRunner

_state = {"file": None, "writer": None, "env": None, "t0": None}
_global_seq = itertools.count(1)
_endpoint_seq = defaultdict(lambda: itertools.count(1))

COLUMNS = ["timestamp", "elapsed_s", "request_no", "endpoint_request_no",
           "endpoint", "status", "category", "message",
           "response_time_ms", "response_length", "users"]


@events.test_start.add_listener
def _open_log(environment, **kwargs):
    if isinstance(environment.runner, MasterRunner):
        return  # the master sends no requests, so it has nothing to log
    prefix = getattr(environment.parsed_options, "csv_prefix", None) or "results/run"
    if isinstance(environment.runner, WorkerRunner):
        prefix += f"_{environment.runner.client_id}"
    f = open(f"{prefix}_requests.csv", "w", newline="", encoding="utf-8", buffering=1)
    writer = csv.writer(f)
    writer.writerow(COLUMNS)
    _state.update(file=f, writer=writer, env=environment, t0=time.time())


@events.request.add_listener
def _log_request(request_type, name, response_time, response_length,
                 response=None, exception=None, start_time=None, **kwargs):
    writer = _state["writer"]
    if writer is None:
        return
    start = start_time or time.time()
    status = getattr(response, "status_code", 0) or 0
    if exception is None:
        category, message = "OK", ""
    else:
        message = str(exception)[:200]
        category = message.split(" ")[0]
    runner = _state["env"].runner
    writer.writerow([
        datetime.fromtimestamp(start).isoformat(timespec="milliseconds"),
        round(start - _state["t0"], 3),
        next(_global_seq),
        next(_endpoint_seq[name]),
        name, status, category, message,
        round(response_time, 1), response_length,
        runner.user_count if runner else "",
    ])


@events.test_stop.add_listener
def _close_log(environment, **kwargs):
    if _state["file"]:
        _state["file"].close()
        _state.update(file=None, writer=None)

@events.quitting.add_listener
def _close_on_quit(environment, **kwargs):
    _close_log(environment)