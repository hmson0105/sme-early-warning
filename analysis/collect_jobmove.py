"""시도별 자발적·비자발적 이직을 수집한다.

실행:  python3 analysis/collect_jobmove.py
산출:  data/jobmove.json

출처:  KOSIS 118/DT_118N_MOND66
       사업체노동력조사 — 행정구역(시도)/산업별(대분류) 고용, 월 단위

왜 이 표인가
  내일채움공제 중도해지는 귀책계약자가 '기업'과 '핵심인력'으로 나뉜다.
  그런데 지역별 해지 통계는 공개되지 않는다. 그래서 같은 구조를 갖는
  공개 지표로 대신한다.

    비자발적이직(emp013)  해고·경영상 이유 → 기업 귀책에 대응
    자발적이직(emp012)    근로자의 자진 퇴사 → 핵심인력 귀책에 대응

  이 표는 공제 가입자만을 센 것이 아니라 지역 전체 사업체를 센 것이다.
  따라서 산출물은 '해지율'이 아니라 '해지 위험 추정'이다. 화면과 문서
  어디에서도 실제 해지율처럼 쓰지 않는다.

주의
  - 지역 코드에 '전남광주통합특별시'(ZONE2017A202000)라는 합산 항목이
    섞여 있다. 광주·전남을 이중으로 세게 되므로 제외한다.
  - 이직률(emp011)은 표가 직접 주지만, 자발적·비자발적은 '명' 단위라
    종사자수(emp002)로 나누어 율로 환산한다.
"""
import json, os, pathlib, sys, time, urllib.parse, urllib.request
from datetime import datetime

HERE = pathlib.Path(__file__).parent
OUT = HERE.parent / "data" / "jobmove.json"

ORG, TBL = "118", "DT_118N_MOND66"
INDUSTRY_ALL = "260225INDUSTRY_11S0"      # 산업 대분류 '전체'
START, END = "202401", "202612"           # 이 표는 2024년 1월부터

# 표시명 → KOSIS 지역 코드. '전남광주통합'은 합산이라 넣지 않는다.
REGIONS = [
    ("서울", "ZONE2017A111100"), ("부산", "ZONE2017A212100"),
    ("대구", "ZONE2017A222200"), ("인천", "ZONE2017A232300"),
    ("광주", "ZONE2017A242400"), ("대전", "ZONE2017A252500"),
    ("울산", "ZONE2017A262600"), ("세종", "ZONE2017A292900"),
    ("경기", "ZONE2017A313100"), ("강원", "ZONE2017A323200"),
    ("충북", "ZONE2017A333300"), ("충남", "ZONE2017A343400"),
    ("전북", "ZONE2017A353500"), ("전남", "ZONE2017A363600"),
    ("경북", "ZONE2017A373700"), ("경남", "ZONE2017A383800"),
    ("제주", "ZONE2017A393900"),
]
NATION = ("전국", "ZONE2017A000000")

ITEMS = {
    "emp002": "workers",       # 종사자(상용+임시일용), 명 — 율 환산의 분모
    "emp010": "sep",           # 이직자, 명
    "emp011": "sep_rate",      # 이직률, %
    "emp012": "quit",          # 자발적이직, 명
    "emp013": "layoff",        # 비자발적이직, 명
}


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
            return json.loads(urllib.request.urlopen(url, timeout=90).read().decode("utf-8"))
        except Exception:
            if n == tries - 1:
                raise
            time.sleep(1.5 * (n + 1))


def fetch(key):
    """시도 × 월 × 항목. objL1 이 지역, objL2 가 산업이다."""
    q = urllib.parse.urlencode({
        "method": "getList", "apiKey": key, "format": "json", "jsonVD": "Y",
        "orgId": ORG, "tblId": TBL,
        "itmId": "+".join(ITEMS),
        "objL1": "ALL", "objL2": INDUSTRY_ALL,
        "prdSe": "M", "startPrdDe": START, "endPrdDe": END,
    })
    rows = get(f"https://kosis.kr/openapi/Param/statisticsParameterData.do?{q}")
    if isinstance(rows, dict):
        raise RuntimeError(rows.get("errMsg", str(rows))[:200])
    return rows


def main():
    key = read_key("KOSIS_API_KEY")
    print(f"KOSIS {ORG}/{TBL} 수집 중 … ({START}~{END})")
    rows = fetch(key)
    print(f"  응답 {len(rows):,}행")

    want = dict(REGIONS + [NATION])
    code2name = {c: n for n, c in REGIONS + [NATION]}

    # {지역: {월: {필드: 값}}}
    grid = {}
    for r in rows:
        code = r.get("C1")
        if code not in code2name:
            continue                       # 합산 지역·기타 코드는 버린다
        field = ITEMS.get(r.get("ITM_ID"))
        if not field:
            continue
        raw = (r.get("DT") or "").replace(",", "").strip()
        if not raw:
            continue
        try:
            v = float(raw)
        except ValueError:
            continue
        grid.setdefault(code2name[code], {}).setdefault(r["PRD_DE"], {})[field] = v

    if not grid:
        sys.exit("수집 결과가 비었습니다. 표 코드와 기간을 확인하세요.")

    months = sorted({m for v in grid.values() for m in v})
    latest = months[-1]
    print(f"  기간 {months[0]} ~ {latest} ({len(months)}개월), 지역 {len(grid)}곳")

    out_regions = {}
    for name, by_month in grid.items():
        series = []
        for m in months:
            d = by_month.get(m)
            if not d:
                continue
            w = d.get("workers")
            rec = {"t": m, "sep_rate": d.get("sep_rate")}
            # '명'을 종사자수로 나눠 율로 바꾼다. 분모가 없으면 비워 둔다.
            if w:
                if d.get("quit") is not None:
                    rec["quit_rate"] = round(d["quit"] / w * 100, 3)
                if d.get("layoff") is not None:
                    rec["layoff_rate"] = round(d["layoff"] / w * 100, 3)
            series.append(rec)
        if series:
            out_regions[name] = series

    missing = [n for n, _ in REGIONS if n not in out_regions]
    if missing:
        print(f"  ⚠ 누락 지역: {', '.join(missing)}")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "citation": f"통계청 KOSIS {ORG}/{TBL} 사업체노동력조사 "
                    f"· 행정구역(시도)/산업별 고용 · 산업 전체 · 월",
        "period": {"start": months[0], "end": latest},
        "note": ("자발적이직률·비자발적이직률은 각 이직자 수를 종사자수"
                 "(상용+임시일용)로 나눈 값이다. 공제 가입자가 아니라 지역 "
                 "전체 사업체 기준이므로 해지율이 아니라 대리지표이다."),
        "regions": out_regions,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    n = out_regions.get("전국", [])
    if n:
        last = n[-1]
        print(f"\n전국 {last['t']}: 이직률 {last.get('sep_rate')}% "
              f"(자발 {last.get('quit_rate')}%, 비자발 {last.get('layoff_rate')}%)")
    print(f"저장: {OUT.name}  {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
