"""
沪深A股代码抓取脚本（不含创业板）
数据来源：
  - 上交所主板/科创板：上交所官方接口
  - 深交所主板/中小板：新浪财经行情接口（深交所官网封锁自动请求）
输出：沪深A股代码（不含创业板）.csv
"""

import requests
import csv
import time
import re

# ── 上交所：主板 + 科创板 ─────────────────────────────────
SH_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "http://www.sse.com.cn/"}

# stock_type: 1=主板A股  8=科创板
SH_STOCK_TYPES = ["1", "8"]


def decode_stock_name(name: str) -> str:
    text = name.strip()
    if "\\u" not in text:
        return text
    try:
        return text.encode("utf-8").decode("unicode_escape")
    except UnicodeDecodeError:
        return text


def is_st_stock(name: str) -> bool:
    normalized = decode_stock_name(name).upper().replace(" ", "")
    return normalized.startswith(("ST", "*ST", "SST", "S*ST"))


def fetch_sse(stock_type: str) -> list[tuple]:
    url = (
        "http://query.sse.com.cn/security/stock/downloadStockListFile.do"
        f"?csrcCode=&stockType={stock_type}"
        "&pageHelp.pageNo=1&pageHelp.beginPage=1&pageHelp.endPage=9999"
    )
    r = requests.get(url, headers=SH_HEADERS, timeout=30)
    r.encoding = "gbk"
    result = []
    for line in r.text.strip().splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip():
            code = parts[0].strip()
            name = parts[1].strip()
            if not is_st_stock(name):
                result.append((code, name, "SH"))
    return result


# ── 深交所：主板 + 中小板（新浪财经 sz_a 节点）────────────
# 深交所官网会封锁自动化请求，改用新浪财经实时行情接口
# 仅保留代码前缀为 000/001/002/003 的主板股票，排除创业板 300/301
SINA_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "http://finance.sina.com.cn/"}
SZSE_MAIN_PATTERN = re.compile(r"^(000|001|002|003)\d{3}$")


def fetch_szse() -> list[tuple]:
    result = []
    page = 1
    while True:
        url = (
            "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
            f"/Market_Center.getHQNodeDataSimple?num=100&page={page}"
            "&sort=symbol&asc=1&node=sz_a"
        )
        try:
            r = requests.get(url, headers=SINA_HEADERS, timeout=20)
            text = r.text.strip()
            if not text or text in ("null", "[]"):
                break
            # 用 json.loads 解析，自动解码 \uXXXX Unicode 转义为中文
            import json
            items = json.loads(text)
            count = 0
            for item in items:
                code = item.get("symbol", "").replace("sz", "").replace("sh", "")
                name = decode_stock_name(item.get("name", ""))
                if SZSE_MAIN_PATTERN.match(code) and not is_st_stock(name):
                    result.append((code, name, "SZ"))
                    count += 1
            if count == 0:
                break
            print(f"  深交所 第{page}页 +{count}，累计 {len(result)}")
            page += 1
            time.sleep(0.4)
        except Exception as e:
            print(f"  深交所 第{page}页出错: {e}，等待重试…")
            time.sleep(3)
            if page > 100:
                break
    return result


# ── 板块标记 ──────────────────────────────────────────────
def board_label(code: str, exchange: str) -> str:
    if exchange == "SH":
        return "科创板" if code.startswith("688") else "沪市主板"
    if code.startswith("002") or code.startswith("003"):
        return "深市中小板"
    return "深市主板"


# ── 主流程 ────────────────────────────────────────────────
def main():
    import os
    out_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(out_dir, "沪深A股代码（不含创业板）.csv")

    print("=== 上交所 ===")
    sh_stocks: list[tuple] = []
    for t in SH_STOCK_TYPES:
        stocks = fetch_sse(t)
        label = "科创板" if t == "8" else "主板"
        print(f"  {label}: {len(stocks)} 只")
        sh_stocks.extend(stocks)
        time.sleep(0.5)
    print(f"上交所合计: {len(sh_stocks)} 只\n")

    print("=== 深交所 ===")
    sz_stocks = fetch_szse()
    print(f"深交所合计: {len(sz_stocks)} 只\n")

    all_stocks = sorted(
        [stock for stock in sh_stocks + sz_stocks if not is_st_stock(stock[1])],
        key=lambda x: x[0],
    )

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["股票代码", "股票名称", "交易所", "板块"])
        for code, name, exch in all_stocks:
            w.writerow([code, name, exch, board_label(code, exch)])

    sh_cnt = sum(1 for s in all_stocks if s[2] == "SH")
    sz_cnt = sum(1 for s in all_stocks if s[2] == "SZ")
    print(f"完成！上交所 {sh_cnt} + 深交所 {sz_cnt} = 共 {len(all_stocks)} 只")
    print(f"已保存: {out_path}")


if __name__ == "__main__":
    main()
