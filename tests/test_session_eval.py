"""Behavioral eval of the sketch worker, off cassettes.

The live predecessor called `sketch.generate("greetings", client=...)` — one
positional argument. The worker has required `scenario` since V2. That
TypeError was as silent as the five-value unpack: same marker, same exclusion.
"""
import pytest

from backend import kb
from backend.models import SketchResult
from backend.workers import sketch
from tests.helpers import cassette_draw_count

pytestmark = pytest.mark.cassette


async def test_sketch_worker_produces_a_valid_result(cassette_client):
    scenario = kb.load_scenario("greetings")
    n = cassette_draw_count(cassette_client)
    for _ in range(n):
        result = await sketch.generate("greetings", scenario, client=cassette_client)

        assert isinstance(result, SketchResult)
        assert result.opening_line.zh and result.opening_line.pinyin
        assert result.sketch.strip()
