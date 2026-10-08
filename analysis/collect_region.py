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
    # 찾는 순서 — 맥·윈도우에서 똑같이 동작하도록 프로젝트 폴더를 먼저 본다.
    #   1) 환경변수
    #   2) 프로젝트 폴더의 keys.env   ← 안내문이 권하는 방법
    #   3) 홈 폴더의 설정 디렉터리     ← 여러 프로젝트가 키를 공유할 때
    here = pathlib.Path(__file__).resolve().parent
    cands = [here / "keys.env", here.parent / "keys.env",
             pathlib.Path(os.path.expanduser("~/.config/sme-dashboard/keys.env")),
             pathlib.Path(os.path.expanduser("~/.config/hmson/keys.env"))]
    p = next((c for c in cands if c.exists()), cands[0])
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            n, v = line.split("=", 1)
            if n.strip() == name and v.strip():
                return v.strip()
    sys.exit(f"{name} 을 찾지 못했습니다. 프로젝트 폴더의 keys.env 를 확인하세요.")


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


def mean_recent(series, months=6):
    """최근 n개월 평균. 단월 값을 쓰면 한 달 급등으로 순위가 뒤집힌다.
    (광주 어음부도율 2026.07 0.12% → 08 2.59%)"""
    vals = [o["v"] for o in series[-months:] if o.get("v") is not None]
    return sum(vals) / len(vals) if vals else None


def add_risk(rows):
    """시도별 신용위험 종합을 계산해 각 행에 risk 로 넣는다.

    연체율과 어음부도율은 단위가 달라 그대로 더할 수 없다. 각각을
    시도 간 z-score 로 바꾼 뒤 평균한다. 둘 다 값이 클수록 위험하므로
    부호를 뒤집을 필요는 없다.

    지도 색칠이 이 값을 읽는다. 이 함수가 빠지면 지도가 무채색이 된다.
    """
    import math

    def z(vals):
        got = {k: v for k, v in vals.items() if v is not None}
        if len(got) < 2:
            return {}
        mu = sum(got.values()) / len(got)
        sd = math.sqrt(sum((v - mu) ** 2 for v in got.values()) / len(got))
        return {k: 0.0 for k in got} if sd == 0 else {k: (v - mu) / sd for k, v in got.items()}

    dl = {r["name"]: mean_recent(r.get("delinq") or []) for r in rows}
    dh = {r["name"]: mean_recent(r.get("dishonor") or []) for r in rows}
    zd, zh = z(dl), z(dh)

    def band(v):
        if v >= 1.0:  return "위험"
        if v >= 0.5:  return "경계"
        if v >= 0.0:  return "주의"
        return "안정"

    # 화면은 {value, level} 형태를 읽는다. 숫자만 넣으면 지도가 검게 나오고
    # 우측 패널이 비면서 멈춘다.
    n = 0
    for r in rows:
        parts = [t[r["name"]] for t in (zd, zh) if r["name"] in t]
        if parts:
            v = round(sum(parts) / len(parts), 3)
            r["risk"] = {"value": v, "level": band(v)}
            n += 1
        else:
            r["risk"] = None
    return n


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

    n_risk = add_risk(out["regions"])
    out["risk_method"] = ("최근 6개월 평균 연체율·어음부도율을 시도 간 z-score 로 "
                          "표준화해 평균. 둘 다 값이 클수록 위험.")
    print(f"\n신용위험 종합 산출: {n_risk}/{len(out['regions'])}개 시도")
    top = sorted((r for r in out["regions"] if r.get("risk") is not None),
                 key=lambda r: -r["risk"]["value"])[:3]
    print("  상위: " + ", ".join(f"{r['name']}({r['risk']['value']:+.2f} {r['risk']['level']})" for r in top))

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n저장: {OUT.name}  {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
