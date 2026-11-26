import csv
import logging

from locust.stats import calculate_response_time_percentile

import smoke_test  # import the module, not the class, so Locust only sees one shape here

HOUR = 3600  # seconds

def window_stats(a, b, settle_time):
    """Stats between two snapshots of (run_time, requests, failures, response_times)."""
    t0, reqs0, fails0, times0 = a
    t1, reqs1, fails1, times1 = b
    reqs = reqs1 - reqs0
    fails = fails1 - fails0
    elapsed = t1 - t0
    times = {rt: n - times0.get(rt, 0) for rt, n in times1.items()}
    times = {rt: n for rt, n in times.items() if n > 0}
    p95 = calculate_response_time_percentile(times, sum(times.values()), 0.95) if times else 0
    return {
        "start_min": (t0 - settle_time) / 60,
        "end_min": (t1 - settle_time) / 60,
        "requests": reqs,
        "error_rate": fails / reqs if reqs else 1.0,
        "rps": reqs / elapsed if elapsed else 0.0,
        "p95_ms": p95,
    }


class SoakTestShape(smoke_test.SmokeTestShape):
    # Soak settings have their own names so they don't override the smoke
    # phase's duration and spawn_rate in the base class.
    soak_users = 120             # 75% of the provisional 160-user knee
    soak_spawn_rate = 2          # users per second
    soak_duration = 4 * HOUR     # measured time, after settling
    interval_duration = 600      # seconds per measurement interval
    settle_time = 180            # seconds ignored once, at the start of the soak

    error_threshold = 0.01       # stop if exceeded in consecutive intervals
    error_consecutive_limit = 2
    hard_error_threshold = 0.05  # stop immediately if one interval exceeds this
    rps_drift_limit = 0.9        # warn if interval RPS < 90% of first hour
    p95_drift_limit = 1.2        # warn if interval p95 > 120% of first hour

    def __init__(self):
        super().__init__()
        self.soak_started = False
        self.snapshots = []      # snapshot at each interval boundary
        self.intervals = []      # stats for each completed interval
        self.first_hour = None
        self.error_streak = 0
        self.drift_warnings = 0

    # ---------- helpers ----------

    def target(self):
        return (self.soak_users, self.soak_spawn_rate)

    def take_snapshot(self, run_time):
        total = self.runner.stats.total
        return (run_time, total.num_requests, total.num_failures, dict(total.response_times))

    @property
    def intervals_per_hour(self):
        return HOUR // self.interval_duration

    def hour_stats(self, first=True):
        n = self.intervals_per_hour
        if len(self.snapshots) <= n:
            return None
        if first:
            a, b = self.snapshots[0], self.snapshots[n]
        else:
            a, b = self.snapshots[-n - 1], self.snapshots[-1]
        return window_stats(a, b, self.settle_time)

    def write_csv(self):
        if not self.intervals:
            return
        prefix = getattr(self.runner.environment.parsed_options, "csv_prefix", None) or "soak"
        path = f"{prefix}_soak_intervals.csv"
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(self.intervals[0].keys()))
                writer.writeheader()
                writer.writerows(self.intervals)
        except OSError as e:
            logging.warning("Could not write %s: %s", path, e)

    def finish(self, reason):
        logging.info("Soak test ended: %s", reason)
        self.write_csv()
        if not self.intervals:
            logging.info("No complete intervals were measured.")
            return None

        logging.info("%-15s %9s %8s %7s %9s", "Interval (min)", "Requests", "Errors", "RPS", "p95 (ms)")
        for i in self.intervals:
            logging.info("%6.0f - %6.0f %9d %7.2f%% %7.1f %9.0f",
                         i["start_min"], i["end_min"], i["requests"],
                         i["error_rate"] * 100, i["rps"], i["p95_ms"])

        whole = window_stats(self.snapshots[0], self.snapshots[-1], self.settle_time)
        checks = [
            ("Ran the full duration", reason == "completed"),
            (f"Overall error rate {whole['error_rate']:.2%} under {self.error_threshold:.0%}",
             whole["error_rate"] < self.error_threshold),
        ]

        n = self.intervals_per_hour
        if len(self.snapshots) > 2 * n:  # first and last hour don't overlap
            first, last = self.hour_stats(first=True), self.hour_stats(first=False)
            checks.append((
                f"Last-hour RPS {last['rps']:.1f} at least {self.rps_drift_limit:.0%} "
                f"of first-hour {first['rps']:.1f}",
                last["rps"] >= self.rps_drift_limit * first["rps"],
            ))
            checks.append((
                f"Last-hour p95 {last['p95_ms']:.0f} ms within {self.p95_drift_limit:.0%} "
                f"of first-hour {first['p95_ms']:.0f} ms",
                last["p95_ms"] <= self.p95_drift_limit * first["p95_ms"],
            ))
        else:
            checks.append(("At least 2 hours measured to compare first and last hour", False))

        for label, ok in checks:
            logging.info("%s: %s", "PASS" if ok else "FAIL", label)
        logging.info("Drift warnings during run: %d", self.drift_warnings)
        logging.info("Soak verdict: %s", "PASS" if all(ok for _, ok in checks) else "FAIL")
        return None

    # ---------- test phase ----------

    def test_tick(self, run_time):
        if not self.soak_started:
            self.runner.stats.reset_all()  # keep smoke-phase requests out of the reports
            self.soak_started = True
            logging.info("Soak starting: %d users, %.1f h measured after a %d s settle",
                         self.soak_users, self.soak_duration / HOUR, self.settle_time)
            return self.target()

        # Settle once at the start, then measure continuously
        if not self.snapshots:
            if run_time >= self.settle_time:
                self.snapshots.append(self.take_snapshot(run_time))
                logging.info("Settle complete, measurement started.")
            return self.target()

        if run_time - self.snapshots[-1][0] < self.interval_duration:
            return self.target()

        # Interval complete: measure it and start the next one immediately
        self.snapshots.append(self.take_snapshot(run_time))
        stats = window_stats(self.snapshots[-2], self.snapshots[-1], self.settle_time)
        self.intervals.append(stats)
        self.write_csv()
        logging.info("Interval %.0f-%.0f min: %d requests, %.2f%% errors, %.1f RPS, p95 %.0f ms",
                     stats["start_min"], stats["end_min"], stats["requests"],
                     stats["error_rate"] * 100, stats["rps"], stats["p95_ms"])

        # Stop only on clear failure
        if stats["error_rate"] > self.hard_error_threshold:
            return self.finish(f"error rate {stats['error_rate']:.2%} above the "
                               f"{self.hard_error_threshold:.0%} hard limit")
        if stats["error_rate"] > self.error_threshold:
            self.error_streak += 1
        else:
            self.error_streak = 0
        if self.error_streak >= self.error_consecutive_limit:
            return self.finish(f"error rate above {self.error_threshold:.0%} for "
                               f"{self.error_streak} consecutive intervals")

        # Drift: warn, but keep running
        if self.first_hour is None:
            self.first_hour = self.hour_stats(first=True)
            if self.first_hour:
                logging.info("First-hour reference: %.1f RPS, p95 %.0f ms",
                             self.first_hour["rps"], self.first_hour["p95_ms"])
        else:
            drift = []
            if stats["rps"] < self.rps_drift_limit * self.first_hour["rps"]:
                drift.append(f"RPS {stats['rps']:.1f} below {self.rps_drift_limit:.0%} of first hour")
            if stats["p95_ms"] > self.p95_drift_limit * self.first_hour["p95_ms"]:
                drift.append(f"p95 {stats['p95_ms']:.0f} ms above {self.p95_drift_limit:.0%} of first hour")
            if drift:
                self.drift_warnings += 1
                logging.warning("Drift in interval %.0f-%.0f min: %s",
                                stats["start_min"], stats["end_min"], "; ".join(drift))

        if run_time - self.settle_time >= self.soak_duration:
            return self.finish("completed")
        return self.target()