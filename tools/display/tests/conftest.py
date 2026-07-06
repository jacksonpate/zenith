import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from zenith_display.runner import Result, Runner


class FakeRunner(Runner):
    """Runner that replays canned results and records every invocation."""

    def __init__(self, responses=None):
        super().__init__(dry_run=False)
        self.responses = responses or {}

    def run(self, argv, timeout=15.0, check=False, mutating=True):
        self.trace.append(list(argv))
        key = argv[0]
        canned = self.responses.get(tuple(argv), self.responses.get(key))
        if canned is None:
            result = Result(argv=argv, returncode=0)
        elif isinstance(canned, Result):
            result = Result(argv=argv, returncode=canned.returncode,
                            stdout=canned.stdout, stderr=canned.stderr)
        else:
            result = Result(argv=argv, returncode=0, stdout=str(canned))
        if check and not result.ok:
            raise RuntimeError(f"command failed: {argv}")
        return result


@pytest.fixture
def fixture_text():
    def _load(name):
        path = os.path.join(os.path.dirname(__file__), "fixtures", name)
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    return _load
