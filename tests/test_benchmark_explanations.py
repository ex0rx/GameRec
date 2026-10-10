"""Explanation HTTP benchmark metrics, failures and bounded concurrency."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from gamerec.scripts import benchmark_explanations as script

CASE = {"case_id": "synthetic", "steam_app_id": 10, "search_query": "Co-op crafting"}


def valid_response(app_id=10):
    return httpx.Response(
        200,
        json={
            "steam_app_id": app_id,
            "game_name": "Synthetic game",
            "explanation": "Co-op crafting matches these interests.",
            "matching_features": ["Co-op"],
        },
    )


def test_existing_case_selection_and_cli_defaults():
    cases = script.benchmark_cases(Path("tests/data/explanation_evaluation_cases.json"))
    assert [case["case_id"] for case in cases] == list(script.CASE_IDS)
    assert cases[-1]["category"] == "less_familiar"
    args = script.parse_args([])
    assert len(cases) * args.repetitions == 20
    assert args.concurrency == [1, 2, 5]
    assert args.warmups == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--repetitions", "0"],
        ["--concurrency", "0"],
        ["--concurrency", "1", "1"],
        ["--warmups", "-1"],
        ["--timeout", "0"],
        ["--timeout", "nan"],
        ["--timeout", "inf"],
        ["--base-url", "ftp://test"],
        ["--base-url", "http://user:password@test"],
    ],
)
def test_invalid_benchmark_options(argv):
    with pytest.raises(SystemExit):
        script.parse_args(argv)


def test_metrics_separate_failures_and_use_total_run_time():
    records = [
        {"succeeded": True, "latency_ms": n, "error_category": None}
        for n in range(1, 21)
    ] + [
        {"succeeded": False, "latency_ms": 500, "error_category": "client_timeout"},
        {
            "succeeded": False,
            "latency_ms": 600,
            "error_category": "service_unavailable",
        },
    ]
    summary = script.summarize_requests(records, 2)
    assert summary["successful_latency_ms"] == {
        "count": 20,
        "mean_ms": 10.5,
        "median_ms": 10.5,
        "p95_ms": 19,
        "min_ms": 1,
        "max_ms": 20,
    }
    assert summary["failed_latency_ms"]["mean_ms"] == 550
    assert summary["failed_count"] == 2
    assert summary["error_rate"] == pytest.approx(2 / 22)
    assert summary["timeout_count"] == 1
    assert summary["other_error_count"] == 1
    assert summary["throughput_rps"] == 11
    assert summary["successful_throughput_rps"] == 10


def test_no_successes_have_unavailable_latency_statistics():
    summary = script.summarize_requests(
        [{"succeeded": False, "latency_ms": 500, "error_category": "server_timeout"}],
        1,
    )
    assert summary["successful_latency_ms"]["count"] == 0
    assert all(
        v is None for k, v in summary["successful_latency_ms"].items() if k != "count"
    )
    assert summary["error_rate"] == 1
    assert summary["timeout_count"] == 1
    assert script.summarize_requests([], 0)["throughput_rps"] is None
    with pytest.raises(ValueError):
        script.summarize_requests([], float("nan"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "status", "error"),
    [
        ("ok", 200, None),
        ("404", 404, "game_not_found"),
        ("422", 422, "invalid_request"),
        ("502", 502, "invalid_model_output"),
        ("503", 503, "service_unavailable"),
        ("504", 504, "server_timeout"),
        ("500", 500, "http_error"),
        ("timeout", None, "client_timeout"),
        ("connect", None, "transport_error"),
        ("bad_json", 200, "invalid_api_response"),
        ("bad_schema", 200, "invalid_api_response"),
        ("wrong_game", 200, "invalid_api_response"),
    ],
)
async def test_request_records_endpoint_payload_and_failure_category(
    outcome, status, error, monkeypatch
):
    requests = []

    def handler(request):
        requests.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("private timeout details")
        if outcome == "connect":
            raise httpx.ConnectError("private network details")
        if outcome == "bad_json":
            return httpx.Response(200, text="private invalid output")
        if outcome == "bad_schema":
            return httpx.Response(200, json={})
        if outcome == "wrong_game":
            return valid_response(999)
        if outcome.isdecimal():
            return httpx.Response(int(outcome), text="private error")
        return valid_response()

    ticks = iter([1.0, 1.25])
    monkeypatch.setattr(
        script, "time", SimpleNamespace(perf_counter=lambda: next(ticks))
    )
    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    ) as client:
        record = await script.request_explanation(client, CASE)
    assert record["http_status"] == status
    assert record["error_category"] == error
    assert record["succeeded"] is (error is None)
    assert record["latency_ms"] == 250
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/explanations/search"
    assert json.loads(requests[0].content) == {
        "steam_app_id": 10,
        "search_query": "Co-op crafting",
    }
    assert "private" not in json.dumps(record)


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2, 5])
async def test_requests_really_overlap_and_remain_bounded(concurrency):
    active = peak = 0
    gate = asyncio.Event()

    async def handler(_):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == concurrency:
            gate.set()
        try:
            await asyncio.wait_for(gate.wait(), timeout=1)
            await asyncio.sleep(0)
            return valid_response()
        finally:
            active -= 1

    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    ) as client:
        level = await script.benchmark_level(
            client, [CASE], repetitions=10, concurrency=concurrency, warmups=0
        )
    assert peak == level["peak_in_flight"] == concurrency
    assert level["overlap_observed"] is (concurrency > 1)
    assert level["successful_count"] == 10
    assert [r["request_index"] for r in level["requests"]] == list(range(10))
    assert all(
        r["finished_offset_ms"] >= r["started_offset_ms"] for r in level["requests"]
    )
    assert level["elapsed_seconds"] > 0


@pytest.mark.asyncio
async def test_failed_warmup_is_recorded_and_excluded_from_measured_metrics():
    count = 0

    def handler(_):
        nonlocal count
        count += 1
        return httpx.Response(503) if count == 1 else valid_response()

    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    ) as client:
        level = await script.benchmark_level(
            client, [CASE], repetitions=4, concurrency=2, warmups=2
        )
    assert count == 6
    assert level["warmups_succeeded"] is False
    assert level["excluded_warmups"][0]["succeeded"] is False
    assert level["request_count"] == level["successful_count"] == 4
    assert level["failed_count"] == 0


@pytest.mark.asyncio
async def test_cli_report_serializes_same_workload_each_level_and_closes_client(
    tmp_path, monkeypatch
):
    async_client = httpx.AsyncClient
    clients = []
    requests = []

    def handler(request):
        requests.append(request)
        return valid_response(json.loads(request.content)["steam_app_id"])

    def factory(**kwargs):
        client = async_client(**kwargs, transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    monkeypatch.setattr(script.httpx, "AsyncClient", factory)
    path = tmp_path / "reports" / "benchmark.json"
    await script.main(["--output", str(path), "--base-url", "http://test"])
    report = json.loads(path.read_text())
    assert clients[0].is_closed
    assert report["request_timeout_seconds"] == clients[0].timeout.read == 240
    assert report["internal_ollama_timings"] is None
    assert report["cold_start"] is None
    assert len(requests) == 3 * (20 + 2)
    workloads = [
        [r["case_id"] for r in level["requests"]] for level in report["levels"]
    ]
    assert workloads[0] == workloads[1] == workloads[2] == list(script.CASE_IDS) * 4
    assert all(level["successful_count"] == 20 for level in report["levels"])
    with pytest.raises(FileExistsError):
        await script.main(["--output", str(path), "--base-url", "http://test"])
