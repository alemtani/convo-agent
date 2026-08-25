"""Cassette flags and the client the behavioral evals share.

`--record` / `--samples` / `--refresh` / `--cassettes` are the same flags the
eval CLI uses. Replay is the default: a miss is an error, never a live call.

The `evals` import is deferred so the smoke job — which path-scopes to
`tests/smoke/` and does not install pydantic — can still collect this file as
a parent conftest without exploding.
"""
import pytest


def pytest_addoption(parser):
    try:
        from evals import cassette
    except ImportError:
        return
    cassette.cli.add_pytest_options(parser)


def pytest_configure(config):
    try:
        record = config.getoption("record")
        refresh = config.getoption("refresh")
    except ValueError:
        return
    if refresh and not record:
        raise pytest.UsageError("--refresh does nothing without --record")


@pytest.fixture
def cassette_client(request):
    """Replay committed recordings; `--record` is the only way to spend."""
    from evals import cassette

    return cassette.cli.client_from_pytest_config(request.config)
