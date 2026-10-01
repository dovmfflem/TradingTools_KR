"""Public KIS commodity master: USD futures, without credentials or orders.

Fields follow KIS stocks_info/domestic_commodity_future_code.py (CP949 text).
One request, connect/read timeouts 5s/10s, download budget 20s, max 5 MiB.
Callers must also bound the whole operation (the app bridge uses 25s).
"""
from io import BytesIO
import re
from time import monotonic
from zipfile import ZipFile

import requests

MASTER_URL = "https://new.real.download.dws.co.kr/common/master/fo_com_code.mst.zip"
MAX_BYTES = 5 * 1024 * 1024


def parse_dollar_futures(payload: bytes) -> list[dict[str, str]]:
    if len(payload) > MAX_BYTES:
        raise ValueError("KIS_MASTER_TOO_LARGE")
    with ZipFile(BytesIO(payload)) as archive:
        member = archive.getinfo("fo_com_code.mst")
        if member.file_size > MAX_BYTES:
            raise ValueError("KIS_MASTER_TOO_LARGE")
        content = archive.read(member).decode("cp949")
    symbols = []
    for line in content.splitlines():
        if not line.strip():
            continue
        if len(line) < 55:
            raise ValueError("KIS_MASTER_INVALID_FORMAT")
        short_code, code, name = line[2:11].strip(), line[11:23].strip(), line[23:55].strip()
        month = re.search(r"20\d{4}", name)
        if not short_code or not code or "달러" not in name or "SP" in name.upper() or not month:
            continue
        symbols.append({"month": month.group(), "short_code": short_code, "name": name, "code": code})
    if not symbols:
        raise ValueError("KIS_MASTER_NO_DOLLAR_FUTURES")
    return sorted(symbols, key=lambda item: (item["month"], item["code"]))


def fetch_dollar_futures() -> list[dict[str, str]]:
    started = monotonic()
    data = bytearray()
    with requests.get(MASTER_URL, timeout=(5, 10), stream=True, allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("KIS_MASTER_HTTP_ERROR")
        for chunk in response.iter_content(chunk_size=65536):
            if monotonic() - started > 20:
                raise TimeoutError("KIS_MASTER_TIMED_OUT")
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise ValueError("KIS_MASTER_TOO_LARGE")
    return parse_dollar_futures(bytes(data))


FUTURE_MASTERS = {"index": "fo_idx_code_mts", "stock": "fo_stk_code_mts", "commodity": "fo_com_code"}


def parse_futures(payload, kind):
    """Outright domestic futures only. Options and calendar spreads are excluded."""
    if kind not in FUTURE_MASTERS or len(payload) > MAX_BYTES:
        raise ValueError("KIS_MASTER_INVALID_FORMAT")
    with ZipFile(BytesIO(payload)) as archive:
        member = archive.getinfo(FUTURE_MASTERS[kind] + ".mst")
        if member.file_size > MAX_BYTES:
            raise ValueError("KIS_MASTER_TOO_LARGE")
        lines = archive.read(member).decode("cp949").splitlines()
    rows = []
    for line in lines:
        if not line.strip():
            continue
        if kind == "commodity":
            if len(line) < 55:
                raise ValueError("KIS_MASTER_INVALID_FORMAT")
            product, symbol, code, name = line[1:2], line[2:11].strip(), line[11:23].strip(), line[23:55].strip()
            underlying = re.split(r"\s+F\s+", name)[0].strip()
        else:
            fields = [field.strip() for field in line.split("|")]
            if len(fields) != 9:
                raise ValueError("KIS_MASTER_INVALID_FORMAT")
            product, symbol, code, name = fields[:4]
            underlying = fields[8]
        month = re.search(r"20\d{4}", name)
        # The index master has separate product codes for mini, volatility,
        # KOSDAQ150, sector and KRX300 futures. "1" alone is only KOSPI200.
        future_types = {"1", "3", "7", "9", "B", "H"} if kind == "index" else {"1"}
        if product not in future_types or not month or "SP" in name.upper() or not re.search(r"F\s+20\d{4}", name):
            continue
        if kind == "index" and "미니" in name:
            underlying = "미니 " + underlying
        if not re.fullmatch(r"[A-Z0-9]{6,9}", symbol):
            raise ValueError("KIS_MASTER_INVALID_SYMBOL")
        rows.append({"short_code": symbol, "code": code, "name": name,
                     "month": month.group(), "kind": kind, "underlying": underlying})
    return sorted(rows, key=lambda row: (row["underlying"], row["month"], row["short_code"]))


def fetch_futures():
    """Three public masters, one shared 20s budget, TLS verification, no retries."""
    deadline, result = monotonic() + 20, []
    for kind, filename in FUTURE_MASTERS.items():
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("KIS_MASTER_TIMED_OUT")
        data = bytearray()
        url = "https://new.real.download.dws.co.kr/common/master/" + filename + ".mst.zip"
        with requests.get(url, timeout=(min(3, remaining), min(5, remaining)), stream=True, allow_redirects=False) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("KIS_MASTER_HTTP_ERROR")
            for chunk in response.iter_content(65536):
                if monotonic() >= deadline:
                    raise TimeoutError("KIS_MASTER_TIMED_OUT")
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError("KIS_MASTER_TOO_LARGE")
        result.extend(parse_futures(bytes(data), kind))
    if not result:
        raise ValueError("KIS_MASTER_EMPTY")
    return result
