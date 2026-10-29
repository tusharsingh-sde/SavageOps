"""Keep unit tests on an explicitly enabled, process-local mock database."""

import os

os.environ["SAVAGEOPS_MOCK"] = "1"
