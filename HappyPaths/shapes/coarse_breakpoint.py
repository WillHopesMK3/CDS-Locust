import logging
from locust import LoadTestShape
from locust.stats import calculate_response_time_percentile


class CoarseBreakpointShape(LoadTestShape):
    start_users = 140
    step_users = 20               # users added each step
    step_duration = 120           # seconds per step
    settle_time = 20              # seconds ignored at the start of each step
    max_users = 5000              # hard stop if nothing breaks
    error_threshold = 0.01        # 1%
    latency_threshold_ms = 14000  # p95 threshold

    def __init__(self):
        super().__init__()
        self.users = self.start_users
        self.prev_users = 0
        self.step_start = 0.0
        self.snapshot = None  # (requests, failures, response_times) when measuring starts
        self.last_passing = None

    def tick(self):
        now = self.get_run_time()
        elapsed = now - self.step_start
        total = self.runner.stats.total

        # Start measuring once this step's users have spawned
        if self.snapshot is None and elapsed >= self.settle_time:
            self.snapshot = (total.num_requests, total.num_failures, dict(total.response_times))

        if elapsed >= self.step_duration:
            start_reqs, start_fails, start_times = self.snapshot
            reqs = total.num_requests - start_reqs
            fails = total.num_failures - start_fails
            error_rate = fails / reqs if reqs else 1.0
            rps = reqs / (self.step_duration - self.settle_time)

            # Response time distribution for this step's measurement window only
            step_times = {rt: n - start_times.get(rt, 0) for rt, n in total.response_times.items()}
            step_times = {rt: n for rt, n in step_times.items() if n > 0}
            p95 = (calculate_response_time_percentile(step_times, sum(step_times.values()), 0.95)
                   if step_times else 0)

            logging.info(
                "Step %d users: %d requests, %.2f%% errors, %.1f RPS, p95 latency %.1f ms",
                self.users, reqs, error_rate * 100, rps, p95,
            )

            if error_rate > self.error_threshold:
                logging.info(
                    "Error threshold breached at %d users. Last passing step: %s users",
                    self.users, self.last_passing,
                )
                return None

            if p95 > self.latency_threshold_ms:
                logging.info(
                    "Latency threshold breached at %d users. Last passing step: %s users",
                    self.users, self.last_passing,
                )
                return None

            self.last_passing = self.users
            self.prev_users = self.users
            self.users += self.step_users
            if self.users > self.max_users:
                logging.info("Reached max_users without breaching. Raise max_users.")
                return None
            self.step_start = now
            self.snapshot = None

        # Spawn each step's new users over roughly 10 seconds
        spawn_rate = max(1, (self.users - self.prev_users) / 10)
        return (self.users, spawn_rate)