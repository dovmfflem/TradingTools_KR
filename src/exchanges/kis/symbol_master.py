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
