"""지역별 산업 구조와 신용 지표를 수집한다.

실행:  python3 analysis/collect_region.py
산출:  data/region.json

세 출처를 시도 단위로 묶는다.
  KOSIS DT_1K52F08  시도·산업별 사업체수·종사자수·매출액 (연간)
  ECOS  141Y005     시도별 중소기업대출 연체율 (월)
  ECOS  801Y003     시도별 어음부도율 (월)

전국 단위 계열에는 중소기업 전용 연체율이 없지만, 지역별 표에는
중소기업대출 연체율(R4AB12)이 따로 있어 이 스크립트에서만 쓸 수 있다.
"""
import json, os, pathlib, sys, time, urllib.parse, urllib.request
from datetime import datetime

HERE = pathlib.Path(__file__).parent
OUT = HERE.parent / "data" / "region.json"

YEAR = "2024"                 # 사업체조사 기준연도
START, END = "201801", "202612"

# 시도 표준화: (표시명, KOSIS C1, ECOS 141Y005 지역코드, ECOS 801Y003 지역코드)
REGIONS = [
    ("서울", "11", "A00", "4020000"), ("부산", "21", "B00", "4030000"),
    ("대구", "22", "C00", "4040000"), ("인천", "23", "D00", "4050000"),
    ("광주", "24", "F00", "4060000"), ("대전", "25", "E00", "4070000"),
    ("울산", "26", "G00", "4080000"), ("세종", "29", None,  None),
    ("경기", "31", "L00", "4110000"), ("강원", "32", "M00", "4120000"),
    ("충북", "33", "N00", "4130000"), ("충남", "34", "P00", "4140000"),
    ("전북", "35", "Q00", "4150000"), ("전남", "36", "R00", "4160000"),
    ("경북", "37", "S00", "4170000"), ("경남", "38", "T00", "4180000"),
    ("제주", "39", "U00", "4190000"),
]

# 지도에서 색으로 볼 주요 산업 (KOSIS 대분류)
INDUSTRIES = [
    ("0",   "전체 산업"), ("C", "제조업"), ("G", "도매 및 소매업"),
    ("I",   "숙박 및 음식점업"), ("F", "건설업"), ("H", "운수 및 창고업"),
    ("J",   "정보통신업"), ("M", "전문·과학 및 기술 서비스업"),
]


def read_key(name):
    k = os.environ.get(name)
    if k:
        return k.strip()
    p = pathlib.Path(os.path.expanduser("~/.config/hmson/keys.env"))
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            n, v = line.split("=", 1)
            if n.strip() == name and v.strip():
                return v.strip()
    sys.exit(f"{name} 을 찾지 못했습니다. ~/.config/hmson/keys.env 를 확인하세요.")


def get(url, tries=3):
    for n in range(tries):
        try:
            return json.loads(urllib.request.urlopen(url, timeout=60).read().decode("utf-8"))
        except Exception:
            if n == tries - 1:
                raise
            time.sleep(1.5 * (n + 1))


def kosis_industry(key):
    """시도 × 산업 × (사업체수·종사자수·매출액)."""
    q = urllib.parse.urlencode({
        "method": "getList", "apiKey": key, "format": "json", "jsonVD": "Y",
        "orgId": "101", "tblId": "DT_1K52F08", "itmId": "ALL",
        "objL1": "ALL", "objL2": "ALL",
        "prdSe": "Y", "startPrdDe": YEAR, "endPrdDe": YEAR,
    })
    rows = get(f"https://kosis.kr/openapi/Param/statisticsParameterData.do?{q}")
    if isinstance(rows, dict):
        raise RuntimeError(rows.get("errMsg", str(rows))[:120])

    want = {c for c, _ in INDUSTRIES}
    out = {}
    for r in rows:
        c1, c2, itm = r.get("C1"), r.get("C2"), r.get("ITM_ID")
        if c2 not in want:
            continue
        v = (r.get("DT") or "").replace(",", "").strip()
        if not v:
            continue
        try:
            v = float(v)
        except ValueError:
            continue
        out.setdefault(c1, {}).setdefault(c2, {})[itm] = v
    return out


def ecos_region(key, stat, item1, region_code, cycle="M"):
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/2000/"
           f"{stat}/{cycle}/{START}/{END}/{item1}")
    if region_code:
        url += f"/{region_code}"
    d = get(url)
    if "RESULT" in d:
        return []
    rows = d[next(iter(d))].get("row", [])
    obs = []
    for r in rows:
        v = (r.get("DATA_VALUE") or "").strip()
        if not v:
            continue
        try:
            obs.append({"t": r["TIME"], "v": float(v)})
        except ValueError:
            continue
    obs.sort(key=lambda o: o["t"])
    return obs


def main():
    kk, ek = read_key("KOSIS_API_KEY"), read_key("ECOS_API_KEY")

    print("KOSIS 전국사업체조사 수집 …")
    ind = kosis_industry(kk)
    print(f"  시도 {len(ind)}개 확보")

    out = {
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "industry_year": YEAR,
        "industries": [{"code": c, "name": n} for c, n in INDUSTRIES],
        "citation": {
            "industry": f"통계청 KOSIS 101/DT_1K52F08 시도·산업별 사업체수·종사자수·매출액 ({YEAR})",
            "delinq": "한국은행 ECOS 141Y005 예금은행 지역별 연체율 · 중소기업대출(1개월 이상)",
            "dishonor": "한국은행 ECOS 801Y003 지역별 어음부도율",
        },
        "regions": [],
    }

    for name, kcode, ecode, dcode in REGIONS:
        row = {"name": name, "kosis": kcode, "industry": {}, "delinq": [], "dishonor": []}
        src = ind.get(kcode, {})
        for c, label in INDUSTRIES:
            v = src.get(c)
            if v:
                row["industry"][c] = {
                    "biz": v.get("T1"), "emp": v.get("T2"), "sales": v.get("T3")
                }
        if ecode:
            row["delinq"] = ecos_region(ek, "141Y005", "R4AB12", ecode)
            time.sleep(0.3)
        if dcode:
            row["dishonor"] = ecos_region(ek, "801Y003", dcode, None)
            time.sleep(0.3)

        biz = row["industry"].get("0", {}).get("biz")
        d = row["delinq"][-1]["v"] if row["delinq"] else None
        print(f"  {name:<3} 사업체 {int(biz) if biz else '-':>9}  "
              f"연체율 {d if d is not None else '-':>5}  "
              f"부도율 {row['dishonor'][-1]['v'] if row['dishonor'] else '-':>5}")
        out["regions"].append(row)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n저장: {OUT.name}  {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
