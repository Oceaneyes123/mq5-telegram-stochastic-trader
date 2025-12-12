from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import json
from datetime import datetime
from pathlib import Path
import os

import re

import anyio
import uvicorn

app = FastAPI()


async def _write_result_file(data: dict) -> None:
    """Persist the last webhook payload to result.json (overwrite)."""
    result_path = Path(__file__).with_name("result.json")
    tmp_path = result_path.with_suffix(".json.tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)

    def _write_sync() -> None:
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(result_path)

    await anyio.to_thread.run_sync(_write_sync)


def _try_parse_mt5_json(raw_text: str) -> tuple[dict | None, str | None, str | None]:
    """Parse MT5 WebRequest JSON.

    MT5 sometimes sends values that are not valid JSON (e.g. unquoted datetimes like
    `bar_time`:2025.12.12 04:05:00). This function tries a normal JSON parse first,
    then applies a few conservative repairs.

    Returns: (payload, repaired_text, error_message)
    """
    try:
        return json.loads(raw_text), None, None
    except Exception as first_error:
        repaired = raw_text

        # Quote MT5 datetime tokens that appear as bare values after ':'
        # Example: "bar_time":2025.12.12 04:05:00 -> "bar_time":"2025.12.12 04:05:00"
        repaired = re.sub(
            r'(:\s*)(\d{4}\.\d{2}\.\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)\b',
            r'\1"\2"',
            repaired,
        )

        # Remove trailing commas before object/array close (non-JSON but common).
        repaired = re.sub(r',\s*([}\]])', r'\1', repaired)

        # Replace non-JSON floats with null.
        repaired = re.sub(r'\bNaN\b', 'null', repaired)
        repaired = re.sub(r'\bInfinity\b', 'null', repaired)
        repaired = re.sub(r'\b-Infinity\b', 'null', repaired)

        try:
            return json.loads(repaired), repaired, None
        except Exception as second_error:
            return None, repaired, f"{first_error} | after_repair: {second_error}"

@app.post("/webhook")
async def webhook_from_mt5(request: Request):
    # Raw body and headers from MT5
    raw_body = await request.body()
    headers = dict(request.headers)

    # Try to parse JSON, but don’t crash if invalid
    raw_text = raw_body.decode("utf-8", "ignore")

    payload, repaired_text, parse_error = _try_parse_mt5_json(raw_text)
    if payload is None:
        print("---- Incoming MT5 WebRequest (INVALID JSON) ----")
        print("Time:   ", datetime.utcnow().isoformat())
        print("Headers:", headers)
        print("Raw:    ", raw_body)
        print("Error:  ", parse_error)
        if repaired_text is not None and repaired_text != raw_text:
            print("Repaired:", repaired_text)
        print("-----------------------------------------------")

        # Keep result.json as valid JSON while honoring "only payload" requirement.
        await _write_result_file({})

        return JSONResponse(
            status_code=400,
            content={
                "status": "invalid_json",
                "error": str(parse_error),
                "raw": raw_text,
                "repaired": repaired_text if repaired_text != raw_text else None,
            },
        )

    # Log valid JSON
    print("---- Incoming MT5 WebRequest ----")
    print("Time:   ", datetime.utcnow().isoformat())
    print("Headers:", headers)
    print("Body:   ", payload)
    print("---------------------------------")

    # Example processing placeholder
    # result = process_mt5_payload(payload)

    response_content = {"status": "ok", "received": payload}

    # Persist only the payload itself (no wrapper object, no "payload" key).
    await _write_result_file(payload)

    return JSONResponse(content=response_content)


@app.get("/result")
async def get_last_result():
    """Return the last payload written to result.json.

    This is useful for quickly checking what the MT5 webhook last sent.
    """
    result_path = Path(__file__).with_name("result.json")

    if not result_path.exists():
        return JSONResponse(
            status_code=404,
            content={"status": "not_found", "detail": "result.json does not exist yet"},
        )

    try:
        raw = result_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:  # pragma: no cover - defensive
        return JSONResponse(
            status_code=500,
            content={
                "status": "invalid_result_json",
                "detail": str(exc),
            },
        )

    return JSONResponse(content=data)


@app.get("/hello")
async def hello_world():
    return JSONResponse(content={"message": "Hello World"})

"""FastAPI application entrypoint.

On Render, make sure the server binds to 0.0.0.0 and uses the
PORT environment variable exposed by the platform.

Recommended Render start command (in the Render dashboard):

    uvicorn server:app --host 0.0.0.0 --port $PORT

You can also run it directly via Python (useful for local testing):

    python server.py
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
