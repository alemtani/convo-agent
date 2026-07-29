#!/usr/bin/env python3
"""Replay harness — WS1 Stage 0's measuring instrument.

POSTs recorded WAVs (or typed phrases) at a *running* server and reports
p50/p95 per stage, so every later stage of WS1 is judged against the same
procedure rather than against whichever turn someone happened to watch.

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
"""
import argparse
import glob
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from backend import timing  # noqa: E402

DEFAULT_WAV = "tests/fixtures/greeting.wav"

# Typed stand-ins for the spoken fixture, cycled through in text mode. Short
# in-band greetings — the same kind of turn the WAVs carry, so the two modes are
# comparable and the difference between them is the speech stages.
TEXT_PHRASES = ["你好", "我叫小明", "你好吗", "我很好，谢谢", "老师好"]


def run_audio_turn(client, base_url, topic_id, wav_path):
    """POST one WAV at /api/turn; return its response body."""
    with open(wav_path, "rb") as f:
        files = {"audio": (os.path.basename(wav_path), f.read(), "audio/wav")}
    resp = client.post(
        f"{base_url}/api/turn",
        files=files,
        data={"topic_id": topic_id, "dialogue": "[]"},
    )
    resp.raise_for_status()
    return resp.json()


def run_text_turn(client, base_url, topic_id, text):
    """POST one typed phrase at /api/turn/text; return its response body."""
    resp = client.post(
        f"{base_url}/api/turn/text",
        json={"topic_id": topic_id, "text": text, "dialogue": []},
    )
    resp.raise_for_status()
    return resp.json()


def stage_sample(body):
    """The turn's timings as a `{stage: ms}` sample for `timing.summarize`."""
    timings = body.get("timings") or {}
    return {
        name: timings.get(f"{name}_ms")
        for name in timing.STAGE_ORDER
        if timings.get(f"{name}_ms") is not None
    }


def report(label, samples, usages):
    """Print the per-stage table plus the cache read that decides Stage 0's
    secondary question — whether the frozen prefix is being reused at all."""
    print(f"\n=== {label} — {len(samples)} turns ===")
    print(timing.format_summary(timing.summarize(samples)))

    reads = [u.get("cache_read_input_tokens") for u in usages if u]
    reads = [r for r in reads if r is not None]
    if reads:
        # Run 1 writes the cache and reads 0; runs 2+ should read the prefix.
        print(
            f"cache_read tokens: first={reads[0]} "
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
            samples, usages, failures = [], [], 0
            for i in range(args.runs):
                try:
                    if mode == "audio":
                        body = run_audio_turn(client, args.url, args.topic,
                                              wavs[i % len(wavs)])
                    else:
                        body = run_text_turn(client, args.url, args.topic,
                                             TEXT_PHRASES[i % len(TEXT_PHRASES)])
                except (httpx.HTTPError, OSError) as exc:
                    failures += 1
                    print(f"  {mode} run {i + 1}: FAILED — {exc}", file=sys.stderr)
                    continue
                samples.append(stage_sample(body))
                usages.append(body.get("usage"))
                stages = " ".join(f"{k}={v:.0f}ms" for k, v in samples[-1].items())
                print(f"  {mode} run {i + 1}/{args.runs}: {stages}")

            results[mode] = {"samples": samples, "usages": usages,
                             "failures": failures}

    for mode, data in results.items():
        report(mode, data["samples"], data["usages"])
        if data["failures"]:
            print(f"{data['failures']} turn(s) failed and are excluded above")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\nraw samples → {args.json_out}")

    return 1 if any(d["failures"] for d in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
