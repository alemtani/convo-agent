#!/usr/bin/env python3
"""Replay harness — WS1's measuring instrument.

POSTs recorded WAVs (or typed phrases) at a *running* server and reports
p50/p95 per stage **and per staged event**, so every step of the latency work is
judged against the same procedure rather than against whichever turn someone
happened to watch.

This hits real Azure and real Claude and therefore costs money — it is a
dev-time tool run by hand, not part of the test suite. The aggregation it
reports lives in `backend.timing` and *is* covered by the suite.

Usage (server running on :8000):

    .venv/bin/python scripts/replay.py --runs 10
    .venv/bin/python scripts/replay.py --wav tests/fixtures/*.wav
    .venv/bin/python scripts/replay.py --mode text --runs 10
    .venv/bin/python scripts/replay.py --mode both --runs 5 --json out.json

Turns are sent **serially** — the point is to measure one turn's latency, and
concurrent turns would contend on the same Azure/Anthropic connections and
report a throughput number dressed up as a latency one.

Each run sends an empty `dialogue`, so every turn is the same shape and the
cached prefix is identical across runs — which also makes `cache_read` on run 2+
a direct read on whether prompt caching is working.

Three tables per mode, because they answer different questions:

- **stage** — how long each stage *ran*. What to make faster.
- **arrival** — how old the turn was when each event *flushed*, measured by the
  server (`elapsed_ms`). What staging actually changed.
- **client arrival** — the same events timed here, from just before the request
  goes out. The server can flush a transcript at 1.3s and still have it reach a
  client at 4.9s if anything between the two buffers the body; only a clock on
  this side can tell those apart.
"""
import argparse
import glob
import json
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from backend import timing  # noqa: E402

DEFAULT_WAV = "tests/fixtures/greeting.wav"

# Typed stand-ins for the spoken fixture, cycled through in text mode. Short
# in-band greetings — the same kind of turn the WAVs carry, so the two modes are
# comparable and the difference between them is the speech stages.
TEXT_PHRASES = ["你好", "我叫小明", "你好吗", "我很好，谢谢", "老师好"]


def run_audio_turn(client, base_url, topic_id, wav_path):
    """Stream one WAV through `/api/turn`; return that turn's sample.

    `/api/turn` answers in NDJSON — one JSON object per line, flushed as each
    stage resolves — so this reads the body incrementally and stamps each line
    on arrival. Reading it with `.json()` would both fail to parse and destroy
    the only client-side evidence that the early events arrive early.
    """
    with open(wav_path, "rb") as f:
        blob = f.read()

    started = time.perf_counter()
    events, client_arrivals = [], {}
    with client.stream(
        "POST",
        f"{base_url}/api/turn",
        files={"audio": (os.path.basename(wav_path), blob, "audio/wav")},
        data={"topic_id": topic_id, "dialogue": "[]"},
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            events.append(event)
            client_arrivals[event.get("stage")] = _ms_since(started)

    return _sample(
        events=events,
        client_arrivals=client_arrivals,
        # `done` is the only event carrying the finished stage table and the
        # usage block; on an `error` turn neither exists, and the turn is a
        # failure rather than a slow success.
        terminal=next(
            (e for e in reversed(events) if e.get("stage") in ("done", "error")),
            None,
        ),
    )


def run_text_turn(client, base_url, topic_id, text):
    """POST one typed phrase at `/api/turn/text`; return that turn's sample.

    Still a collected JSON body — with one worker call there is nothing to
    stage. Its only arrival is the whole round trip, recorded as `done` so the
    two modes line up in the same table.
    """
    started = time.perf_counter()
    resp = client.post(
        f"{base_url}/api/turn/text",
        json={"topic_id": topic_id, "text": text, "dialogue": []},
    )
    resp.raise_for_status()
    body = resp.json()
    return _sample(
        events=[],
        client_arrivals={"done": _ms_since(started)},
        terminal=body,
    )


def _ms_since(started):
    return round((time.perf_counter() - started) * 1000, 1)


def _sample(*, events, client_arrivals, terminal):
    """One turn's contribution to the report."""
    terminal = terminal or {}
    return {
        "stages": timing.stage_sample(terminal.get("timings")),
        "arrivals": timing.arrival_sample(events),
        "client_arrivals": client_arrivals,
        "usage": terminal.get("usage"),
        # An in-band `error` event is a failed turn that already spent a status
        # line on 200. Counting it as a slow success would quietly bias the p50.
        "error": terminal.get("detail") if terminal.get("stage") == "error" else None,
    }


def report(label, samples, usages):
    """Print the three tables plus the token line.

    `cache_read` decides the question the frozen prefix exists for — whether it
    is being reused at all — and output tokens are the lever the hot-path output
    cut moves, so both are printed next to the timings they explain.
    """
    print(f"\n=== {label} — {len(samples)} turns ===")
    print("stage durations")
    print(timing.format_summary(timing.summarize(s["stages"] for s in samples)))

    arrivals = timing.summarize(s["arrivals"] for s in samples)
    if arrivals:
        print("\nevent arrival (server elapsed_ms)")
        print(timing.format_summary(arrivals, order=timing.EVENT_ORDER))

    client_arrivals = timing.summarize(s["client_arrivals"] for s in samples)
    if client_arrivals:
        print("\nevent arrival (client-side)")
        print(timing.format_summary(client_arrivals, order=timing.EVENT_ORDER))

    reads = [u.get("cache_read_input_tokens") for u in usages if u]
    reads = [r for r in reads if r is not None]
    if reads:
        # Run 1 writes the cache and reads 0; runs 2+ should read the prefix.
        print(
            f"\ncache_read tokens: first={reads[0]} "
            f"rest_min={min(reads[1:], default=0)} rest_max={max(reads[1:], default=0)}"
        )
    ins = [u.get("input_tokens") for u in usages if u and u.get("input_tokens")]
    outs = [u.get("output_tokens") for u in usages if u and u.get("output_tokens")]
    if ins:
        print(f"tokens: in p50={timing.percentile(ins, 50)} "
              f"out p50={timing.percentile(outs, 50)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000",
                        help="base URL of a running server")
    parser.add_argument("--mode", default="audio",
                        choices=("audio", "text", "both"),
                        help="which loop to measure (default: audio)")
    parser.add_argument("--wav", nargs="*", default=None,
                        help=f"WAV files to replay (default: {DEFAULT_WAV}, repeated)")
    parser.add_argument("--runs", type=int, default=10,
                        help="turns per mode; WAVs/phrases cycle to fill it")
    parser.add_argument("--topic", default="greetings")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--label", default=None,
                        help="tag the run in the report and the JSON dump")
    parser.add_argument("--json", dest="json_out", default=None,
                        help="also write the raw per-turn samples here")
    args = parser.parse_args(argv)

    wavs = []
    for pattern in (args.wav or [DEFAULT_WAV]):
        wavs.extend(sorted(glob.glob(pattern)) or [pattern])
    missing = [w for w in wavs if not os.path.exists(w)]
    if args.mode in ("audio", "both") and missing:
        parser.error(f"no such WAV: {', '.join(missing)}")

    results = {}
    with httpx.Client(timeout=args.timeout) as client:
        for mode in (("audio", "text") if args.mode == "both" else (args.mode,)):
            samples, failures = [], 0
            for i in range(args.runs):
                try:
                    if mode == "audio":
                        sample = run_audio_turn(client, args.url, args.topic,
                                                wavs[i % len(wavs)])
                    else:
                        sample = run_text_turn(client, args.url, args.topic,
                                               TEXT_PHRASES[i % len(TEXT_PHRASES)])
                except (httpx.HTTPError, OSError, ValueError) as exc:
                    failures += 1
                    print(f"  {mode} run {i + 1}: FAILED — {exc}", file=sys.stderr)
                    continue
                if sample["error"]:
                    failures += 1
                    print(f"  {mode} run {i + 1}: ERROR EVENT — {sample['error']}",
                          file=sys.stderr)
                    continue
                samples.append(sample)
                stages = " ".join(f"{k}={v:.0f}ms"
                                  for k, v in sample["stages"].items())
                print(f"  {mode} run {i + 1}/{args.runs}: {stages}")

            results[mode] = {"samples": samples, "failures": failures}

    label = f" [{args.label}]" if args.label else ""
    for mode, data in results.items():
        report(mode + label, data["samples"],
               [s["usage"] for s in data["samples"]])
        if data["failures"]:
            print(f"{data['failures']} turn(s) failed and are excluded above")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"label": args.label, "results": results}, f,
                      ensure_ascii=False, indent=2)
        print(f"\nraw samples → {args.json_out}")

    return 1 if any(d["failures"] for d in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
