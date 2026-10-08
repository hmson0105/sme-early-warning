"""내일채움공제 해지 위험을 시도별로 추정한다.

실행:  python3 analysis/build_nacham.py
       (collect_jobmove.py, collect_region.py 를 먼저 실행해야 한다)
산출:  data/nacham.json

입력
  공공데이터포털 15040397  내일채움공제 중도해지 사유별 현황 (전국, 연간)
  data/jobmove.json        시도별 자발적·비자발적 이직률 (KOSIS)
  data/region.json         시도별 중소기업 연체율·어음부도율 (ECOS)

무엇을 하는가
  지역별 해지 통계는 공개되지 않는다. 공개된 것은 '전국 해지 사유별 건수'
  뿐이다. 그래서 두 단계로 나눈다.

    1단계  전국 실적으로 '무엇 때문에 해지되는가'의 비중을 구한다.
    2단계  그 사유에 대응하는 공개 지역지표를 1단계 비중으로 가중합한다.

  가중치를 임의로 정하지 않고 실제 해지 건수에서 끌어온다는 점이 핵심이다.

사유와 지역지표의 대응
    이직·창업에 의한 퇴직        → 자발적이직률      (KOSIS)
    권고사직 등 기업사유 퇴직      → 비자발적이직률     (KOSIS)
    경제적 부담(기업)·폐업·미납   → 연체율·어음부도율  (ECOS)

  나머지 사유(개인 경제적 부담, 학업, 기타 등)는 대응하는 지역 공개지표가
  없다. 전체 해지의 약 3분의 1에 해당하며, 지수에 반영되지 않는다.

한계 — 산출물에 반드시 함께 표기한다
  - 이 수치는 해지율이 아니라 '해지 위험 추정'이다. 이직률은 공제 가입자가
    아니라 지역 전체 사업체를 센 값이다.
  - 전국 사유 비중이 모든 지역에서 같다고 가정한다. 검증할 자료가 없다.
  - 상관이지 인과가 아니다. 순위는 점검 우선순위를 정하는 참고치로만 쓴다.
"""
import csv, io, json, math, pathlib, sys, urllib.request
from datetime import datetime

HERE = pathlib.Path(__file__).parent
DATA = HERE.parent / "data"
CACHE = HERE / "_nacham_src.csv"
CACHE2 = HERE / "_nacham_apply.csv"
OUT = DATA / "nacham.json"

# 공공데이터포털 15040397 의 첨부파일. 포털이 파일을 갱신하면 이 id 가 바뀐다.
SRC_URL = ("https://www.data.go.kr/cmm/cmm/fileDownload.do"
           "?atchFileId=FILE_000000003680628&fileDetailSn=1&insertDataPrcus=N")
SRC_PAGE = "https://www.data.go.kr/data/15040397/fileData.do"

# 공공데이터포털 15133146 중도해지 신청 건별 명세(약 8.3만 건).
# 청약번호 앞 7자리가 가입 연월이라 '가입 후 몇 개월 만에 해지했는가'를
# 구할 수 있다. 지급예정일자는 해지 신청 이후 시점이므로 해지 시기의
# 근사치로만 쓴다.
SRC2_URL = ("https://www.data.go.kr/cmm/cmm/fileDownload.do"
            "?atchFileId=FILE_000000003680683&fileDetailSn=1&insertDataPrcus=N")
SRC2_PAGE = "https://www.data.go.kr/data/15133146/fileData.do"

# 해지 사유 → 대리지표. 사유명은 원본 CSV 표기를 그대로 쓴다.
PROXY = {
    "quit":   {  # 근로자가 스스로 떠난 경우
        "label": "자발적 이직",
        "field": "quit_rate",
        "reasons": ["이직에 의한 퇴직", "창업에 의한 퇴직"],
    },
    "layoff": {  # 기업이 내보낸 경우
        "label": "권고사직·해고",
        "field": "layoff_rate",
        "reasons": ["권고사직 등 기업사유에 의한 퇴직"],
    },
    "firm":   {  # 기업이 못 버틴 경우
        "label": "기업 재무 악화",
        "field": None,                      # 연체율·어음부도율을 평균
        "reasons": ["경제적 부담(기업)", "중소기업의 폐업 또는 해산",
                    "연속 6개월 이상 미납(기업)"],
    },
}


def load_reasons():
    """해지 사유별 건수를 내려받아(최초 1회) 사유명 → 건수로 집계한다."""
    if not CACHE.exists():
        print("해지 사유 CSV 내려받는 중 …")
        req = urllib.request.Request(SRC_URL, headers={
            "User-Agent": "Mozilla/5.0", "Referer": SRC_PAGE})
        CACHE.write_bytes(urllib.request.urlopen(req, timeout=90).read())

    text = CACHE.read_bytes().decode("cp949")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "사유" not in rows[0]:
        sys.exit(f"CSV 형식이 예상과 다릅니다. {CACHE} 를 지우고 다시 실행하세요.")

    by_reason, by_blame = {}, {}
    for r in rows:
        n = int(r["건수"])
        by_reason[r["사유"]] = by_reason.get(r["사유"], 0) + n
        by_blame[r["귀책계약자"]] = by_blame.get(r["귀책계약자"], 0) + n
    return by_reason, by_blame, sum(by_reason.values())



def load_applications():
    """해지 신청 명세로 '언제·얼마에서 해지되는가'를 구한다.

    담당자가 알아야 할 것은 지역만이 아니다. 가입 후 어느 시점이
    위험한지, 어느 달에 신청이 몰리는지를 알아야 안내 시점을 잡는다.

    주의 — 납입금액 두 열은 포털 설명이 '해지 전까지 납입한 금액'이나,
    유지 기간이 3개월이든 61개월이든 값이 거의 같다(핵심인력 중앙값
    10만 원 고정). 누적액이 아니라 월 납입 약정액으로 보는 것이 맞다.
    따라서 누적 적립은 월납입액 × 유지개월의 추정으로만 제시한다.
    """
    if not CACHE2.exists():
        print("해지 신청 명세 CSV 내려받는 중 … (약 3MB)")
        req = urllib.request.Request(SRC2_URL, headers={
            "User-Agent": "Mozilla/5.0", "Referer": SRC2_PAGE})
        CACHE2.write_bytes(urllib.request.urlopen(req, timeout=180).read())

    rows = list(csv.DictReader(io.StringIO(CACHE2.read_bytes().decode("cp949"))))
    import re
    dur, by_mon, pay_e, pay_f = [], {}, [], []
    for r in rows:
        a = re.match(r"(\d{4})-(\d{2})", r.get("청약번호") or "")
        b = re.match(r"(\d{4})-(\d{2})", r.get("지급예정일자") or "")
        if not (a and b):
            continue
        months = (int(b.group(1)) - int(a.group(1))) * 12 + (int(b.group(2)) - int(a.group(2)))
        if not (0 <= months <= 200):
            continue
        dur.append(months)
        by_mon[int(b.group(2))] = by_mon.get(int(b.group(2)), 0) + 1
        try:
            pay_e.append(int(r["핵심인력납입금액"] or 0))
            pay_f.append(int(r["중소기업납입금액"] or 0))
        except ValueError:
            pass

    if not dur:
        return None

    buckets = [(0, 6, "6개월 이내"), (6, 12, "6~12개월"), (12, 24, "1~2년"),
               (24, 36, "2~3년"), (36, 60, "3~5년"), (60, 999, "5년 초과")]
    n = len(dur)
    tenure = [{"label": lab, "n": sum(1 for d in dur if lo <= d < hi),
               "share": round(sum(1 for d in dur if lo <= d < hi) / n, 4)}
              for lo, hi, lab in buckets]

    tot_mon = sum(by_mon.values())
    season = [{"m": m, "n": by_mon.get(m, 0),
               "share": round(by_mon.get(m, 0) / tot_mon, 4)} for m in range(1, 13)]

    med = lambda a: sorted(a)[len(a) // 2] if a else None
    return {
        "n": n,
        "tenure": tenure,
        "within_1y": round(sum(1 for d in dur if d < 12) / n, 4),
        "season": season,
        "peak_month": max(season, key=lambda x: x["n"])["m"],
        "low_month": min(season, key=lambda x: x["n"])["m"],
        "monthly_pay": {"employee": med(pay_e), "firm": med(pay_f),
                        "total": (med(pay_e) or 0) + (med(pay_f) or 0)},
        "citation": "공공데이터포털 15133146 중소벤처기업진흥공단 "
                    "내일채움공제 중도해지신청현황 (일반 상품 기준)",
        "caveat": ("유지 기간은 청약번호의 가입 연월과 지급예정일자의 차이로 "
                   "계산한 근사치. 납입금액 두 열은 유지 기간과 무관하게 거의 "
                   "일정하여 누적액이 아니라 월 납입 약정액으로 판단된다."),
    }


def latest_mean(series, field, months=6):
    """최근 n개월 평균. 단월 급등에 순위가 흔들리지 않게 한다."""
    vals = [r[field] for r in series[-months:] if r.get(field) is not None]
    return sum(vals) / len(vals) if vals else None


def zscores(d):
    """{지역: 값} → {지역: z}. 값이 없는 지역은 빠진다."""
    vals = [v for v in d.values() if v is not None]
    if len(vals) < 2:
        return {}
    mu = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals))
    if sd == 0:
        return {k: 0.0 for k in d if d[k] is not None}
    return {k: (v - mu) / sd for k, v in d.items() if v is not None}


def main():
    by_reason, by_blame, total = load_reasons()

    # ── 1단계: 대리지표가 있는 사유만 모아 가중치를 만든다 ──
    weights, mapped = {}, 0
    for k, spec in PROXY.items():
        n = sum(by_reason.get(r, 0) for r in spec["reasons"])
        weights[k] = n
        mapped += n
    if mapped == 0:
        sys.exit("사유명이 하나도 일치하지 않습니다. 원본 CSV 표기를 확인하세요.")
    weights = {k: v / mapped for k, v in weights.items()}

    print("── 전국 해지 실적 (공공데이터포털 15040397)")
    print(f"   총 {total:,}건")
    for who, n in sorted(by_blame.items(), key=lambda x: -x[1]):
        print(f"     {who:<6} {n:>8,}  {n/total*100:5.1f}%")
    print(f"\n── 지역지표로 설명 가능한 사유: {mapped:,}건 ({mapped/total*100:.1f}%)")
    for k, w in weights.items():
        print(f"     {PROXY[k]['label']:<14} 가중치 {w:.3f}")

    # ── 2단계: 지역지표를 불러와 표준화·가중합 ──
    jm = json.loads((DATA / "jobmove.json").read_text(encoding="utf-8"))
    rg = json.loads((DATA / "region.json").read_text(encoding="utf-8"))

    regions = [r for r in jm["regions"] if r != "전국"]
    raw = {k: {} for k in PROXY}

    for name in regions:
        s = jm["regions"][name]
        raw["quit"][name] = latest_mean(s, "quit_rate")
        raw["layoff"][name] = latest_mean(s, "layoff_rate")

    # 기업 재무 악화 = 연체율 z 와 어음부도율 z 의 평균.
    # 노동지표와 똑같이 최근 6개월 평균을 쓴다. 단월 값을 쓰면 어음부도율이
    # 한 달 튀는 것만으로 순위가 뒤집힌다(광주 2026.07 0.12% → 08 2.59%).
    delinq = {r["name"]: latest_mean(r.get("delinq") or [], "v")
              for r in rg["regions"] if r["name"] in regions}
    dishon = {r["name"]: latest_mean(r.get("dishonor") or [], "v")
              for r in rg["regions"] if r["name"] in regions}
    zd, zh = zscores(delinq), zscores(dishon)
    for name in regions:
        parts = [z[name] for z in (zd, zh) if name in z]
        raw["firm"][name] = sum(parts) / len(parts) if parts else None

    z = {k: zscores(v) for k, v in raw.items()}

    scores = {}
    for name in regions:
        acc, wsum = 0.0, 0.0
        for k, w in weights.items():
            if name in z[k]:
                acc += w * z[k][name]
                wsum += w
        if wsum >= 0.5:                      # 절반 이상 채워진 지역만 점수화
            scores[name] = round(acc / wsum, 3)

    def band(v):
        if v is None:   return "자료부족"
        if v >= 1.0:    return "높음"
        if v >= 0.5:    return "주의"
        if v >= 0.0:    return "보통"
        return "낮음"

    out_regions = []
    for name in regions:
        out_regions.append({
            "name": name,
            "score": scores.get(name),
            "band": band(scores.get(name)),
            "quit_rate": raw["quit"].get(name) and round(raw["quit"][name], 3),
            "layoff_rate": raw["layoff"].get(name) and round(raw["layoff"][name], 3),
            "delinq": delinq.get(name) and round(delinq[name], 3),
            "dishonor": dishon.get(name) and round(dishon[name], 3),
        })
    out_regions.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))

    apps = load_applications()
    if apps:
        print(f"\n── 해지 타이밍 (신청 {apps['n']:,}건)")
        print(f"   가입 1년 이내 해지 {apps['within_1y']*100:.1f}%")
        for t in apps["tenure"]:
            print(f"     {t['label']:<9} {t['n']:>7,}  {t['share']*100:5.1f}%")
        print(f"   신청 최다 {apps['peak_month']}월 / 최소 {apps['low_month']}월")
        mp = apps["monthly_pay"]
        print(f"   월 납입 중앙값 — 핵심인력 {mp['employee']:,}원 · 기업 {mp['firm']:,}원")

    OUT.write_text(json.dumps({
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "applications": apps,
        "national": {
            "total": total,
            "by_blame": by_blame,
            "by_reason": dict(sorted(by_reason.items(), key=lambda x: -x[1])),
            "citation": "공공데이터포털 15040397 중소벤처기업진흥공단 "
                        "내일채움공제 중도해지 사유별 현황 (2026-06-30 기준)",
        },
        "weights": {k: {"label": PROXY[k]["label"], "w": round(w, 4),
                        "reasons": PROXY[k]["reasons"]}
                    for k, w in weights.items()},
        "coverage": round(mapped / total, 4),
        "window": "최근 6개월 평균",
        "sources": {
            "jobmove": jm["citation"],
            "credit": rg["citation"]["delinq"] + " / " + rg["citation"]["dishonor"],
        },
        "caveat": ("해지율이 아니라 해지 위험 추정이다. 이직률은 공제 가입자가 "
                   "아니라 지역 전체 사업체 기준이며, 전국 사유 비중이 모든 "
                   "지역에서 같다고 가정한다. 점검 우선순위 참고용이다."),
        "regions": out_regions,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print("\n── 해지 위험 추정 상위")
    for r in out_regions[:6]:
        if r["score"] is None: continue
        print(f"   {r['name']:<4} {r['score']:+.2f} ({r['band']})  "
              f"자발 {r['quit_rate']}%  비자발 {r['layoff_rate']}%  "
              f"연체 {r['delinq']}%")
    print(f"\n저장: {OUT.name}  {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
