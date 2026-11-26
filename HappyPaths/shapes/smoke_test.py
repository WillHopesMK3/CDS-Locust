import logging
from locust import LoadTestShape
from locustfile import completed_tasks

class SmokeTestShape(LoadTestShape):
    abstract = True

    smoke_users = 5
    duration = 120 # seconds
    spawn_rate = 1 # users per second

    expected_tasks = {
        "CalculateLandedCost",
        "LinkS10ID",
        "QueryDeclaration",
        "CreateManifest",
        "CalculateLandedCost [missing country]",
        "CalculateLandedCost [country as number]",
        "CalculateLandedCost [invalid token]",
        "QueryDeclaration [unknown id]",
        "LinkS10ID [already linked]",
        "QueryDeclaration [hsCode assigned]",
        "CreateManifest [EU IOSS]",
        "CalculateLandedCost [no hsCode]",
    }

    def __init__(self):
        super().__init__()
        self.start_users = self.smoke_users
        self.phase = "smoke"
        self.smoke_start_time = None
        self.test_start_time = None


    def tick(self):

        if self.phase == "smoke":
            total = self.runner.stats.total

            if self.smoke_start_time is None:
                self.smoke_start_time = self.get_run_time()
                logging.info("Smoke test started.")

            if total.num_failures > 0:
                logging.info(
                    "Failures detected during smoke test. Total failures: %d",
                    total.num_failures,
                )
                return None

            if self.get_run_time() > self.duration:
                if not (self.expected_tasks - completed_tasks):
                    logging.info(
                        "Smoke test completed successfully. All expected tasks completed."
                    )
                    self.phase = "test"
                    self.test_start_time = self.get_run_time()
                    return self.test_tick(0)
                else:
                    logging.info(
                        "Smoke test completed, but not all expected tasks were completed. "
                        "Missing tasks: %s"
                        "\nContinuing...",

                        self.expected_tasks - completed_tasks
                    )
                    return (self.start_users, self.spawn_rate)
            return (self.start_users, self.spawn_rate)

        elif self.phase == "test":
            run_time = self.get_run_time() - self.test_start_time
            return self.test_tick(run_time)

    def test_tick(self, run_time):
        """
        Override this in your actual test shape.
        
        """

        raise NotImplementedError("test_tick() must be implemented in the subclass.")