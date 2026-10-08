"""한국은행 ECOS·통계청 KOSIS 오픈API에서 중소기업 위기 지표를 수집한다.

실행:  python3 analysis/collect_ecos.py
산출:  analysis/ecos_result.json  (대시보드가 읽는 값)

API 키는 저장소에 두지 않는다. ~/.config/hmson/keys.env 의
ECOS_API_KEY / KOSIS_API_KEY 또는 동명의 환경변수에서 읽는다. 이 파일은 공개
저장소에 커밋되므로 키가 들어가면 그대로 노출된다.

수집 계열은 아래 SERIES 에 통계표코드·항목코드로 명시했다.
값을 손으로 옮겨 적지 않고, 호출 결과를 그대로 저장한다.
"""
import json, os, pathlib, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime

BASE = "https://ecos.bok.or.kr/api"
OUT = pathlib.Path(__file__).parent / "ecos_result.json"

# 조회 구간 — 월별 계열 기준
START, END = "201801", "202612"

# 일부 통계표는 2차원이다. 항목코드를 하나만 주면 2차원의 모든 값이
# 한 계열에 섞여 들어오므로(예: 연체율은 은행전체·일반은행·특수은행이
# 뒤섞인다) item2 까지 명시한다.
# (키, 표시명, 통계표, 항목1, 항목2, 주기, 단위, 출처표기)
# ── 지표 풀 ──────────────────────────────────────────────
# 중소기업 부실과 연관될 수 있는 후보 지표를 넓게 모은다. 어느 것이
# 실제로 연체율과 함께 움직이는지는 대시보드의 상관 분석에서 가린다.
# 일부 표는 2차원이라 항목을 하나만 주면 값이 섞이므로 item2 까지 명시한다.
# dir: 값이 커질 때 위험이 커지면 +1, 작아질 때 위험이 커지면 -1
# (키, 표시명, 통계표, 항목1, 항목2, 단위, 그룹, 방향, 출처)
SERIES = [
    ("delinq_corp", "기업대출 연체율", "901Y054", "MO3AA", "AB", "%", "자금·신용", +1,
     "ECOS 901Y054 은행대출금 연체율(1일 이상) · 기업대출×은행전체"),
    ("delinq_house", "가계대출 연체율", "901Y054", "MO3AB", "AB", "%", "자금·신용", +1,
     "ECOS 901Y054 · 가계대출×은행전체"),
    ("rate_sme", "중소기업 대출금리", "121Y015", "BECBLB020102", None, "연 %", "자금·신용", +1,
     "ECOS 121Y015 예금은행 대출금리(잔액) · 중소기업대출"),
    ("rate_large", "대기업 대출금리", "121Y015", "BECBLB020101", None, "연 %", "자금·신용", +1,
     "ECOS 121Y015 · 대기업대출"),
    ("base_rate", "한국은행 기준금리", "722Y001", "0101000", None, "%", "자금·신용", +1,
     "ECOS 722Y001 한국은행 기준금리"),
    ("dishonor", "어음부도율", "801Y002", "1090000", None, "%", "자금·신용", +1,
     "ECOS 801Y002 어음교환 및 부도 · 금액기준, 전자결제분 제외"),
    ("bsi_fund_now", "중소기업 자금사정 BSI(실적)", "512Y013", "X6000", "AO", "지수", "자금·신용", -1,
     "ECOS 512Y013 기업경기조사(실적) · 중소기업×자금사정"),
    ("bsi_fund_out", "중소기업 자금사정 BSI(전망)", "512Y014", "X6000", "BO", "지수", "자금·신용", -1,
     "ECOS 512Y014 기업경기조사(전망) · 중소기업×자금사정"),
    ("bsi_biz_now", "중소기업 업황 BSI(실적)", "512Y013", "X6000", "AA", "지수", "수요·심리", -1,
     "ECOS 512Y013 · 중소기업×업황실적"),
    ("bsi_biz_out", "중소기업 업황 BSI(전망)", "512Y014", "X6000", "BA", "지수", "수요·심리", -1,
     "ECOS 512Y014 · 중소기업×업황전망"),
    ("bsi_sales", "중소기업 매출 BSI(실적)", "512Y013", "X6000", "AB", "지수", "수요·심리", -1,
     "ECOS 512Y013 · 중소기업×매출실적"),
    ("bsi_nonmfg", "비제조업 업황 BSI(실적)", "512Y013", "Y9900", "AA", "지수", "수요·심리", -1,
     "ECOS 512Y013 · 비제조업×업황실적"),
    ("esi", "경제심리지수", "513Y001", "E1000", None, "지수", "수요·심리", -1,
     "ECOS 513Y001 경제심리지수 · 원계열"),
    ("esi_cycle", "경제심리지수 순환변동치", "513Y001", "E2000", None, "지수", "수요·심리", -1,
     "ECOS 513Y001 · 순환변동치"),
    ("csi", "소비자심리지수", "511Y002", "FME", "99988", "지수", "수요·심리", -1,
     "ECOS 511Y002 소비자동향조사 · 소비자심리지수"),
    ("csi_now", "현재경기판단 CSI", "511Y002", "FMAB", "99988", "지수", "수요·심리", -1,
     "ECOS 511Y002 · 현재경기판단"),
    ("production", "전산업생산지수", "901Y033", "A00", "1", "2020=100", "실물·생산", -1,
     "ECOS 901Y033 전산업생산지수(농림어업 제외) · 원계열"),
    ("capex", "설비투자지수", "901Y066", "I15A", None, "2020=100", "실물·생산", -1,
     "ECOS 901Y066 설비투자지수 · 원지수"),
    ("inventory", "제조업 재고율", "901Y026", "I33A", None, "%", "실물·생산", +1,
     "ECOS 901Y026 제조업 재고율"),
    ("operation", "제조업 평균가동률", "901Y025", "I31A", None, "%", "실물·생산", -1,
     "ECOS 901Y025 제조업 평균가동률"),
    ("export", "수출금액", "901Y118", "T002", None, "천달러", "대외·가격", -1,
     "ECOS 901Y118 수출입 총괄 · 수출금액"),
    ("import", "수입금액", "901Y118", "T004", None, "천달러", "대외·가격", +1,
     "ECOS 901Y118 · 수입금액"),
]


def read_key(name="ECOS_API_KEY", required=True):
    k = os.environ.get(name)
    if k:
        return k.strip()
    p = pathlib.Path(os.path.expanduser("~/.config/hmson/keys.env"))
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            name_, val = line.split("=", 1)
            if name_.strip() == name and val.strip():
                return val.strip()
    if not required:
        return None
    sys.exit(
        f"{name} 을 찾지 못했습니다.\n"
        f"  ~/.config/hmson/keys.env 에 {name}=... 를 넣거나\n"
        "  환경변수로 지정하세요. 키를 이 파일에 직접 적지 마세요."
    )


def fetch(key, stat, item, item2, cycle, start, end, tries=3):
    """ECOS StatisticSearch 호출. 일시적 오류는 재시도한다."""
    url = f"{BASE}/StatisticSearch/{key}/json/kr/1/2000/{stat}/{cycle}/{start}/{end}/{item}"
    if item2:
        url += f"/{item2}"
    for n in range(tries):
        try:
            raw = urllib.request.urlopen(url, timeout=40).read().decode("utf-8")
            d = json.loads(raw)
            # ECOS 는 오류도 200 으로 주고 본문에 RESULT 를 담는다.
            if "RESULT" in d:
                code = d["RESULT"].get("CODE", "")
                msg = d["RESULT"].get("MESSAGE", "")
                if code == "INFO-200":          # 해당 자료 없음 — 재시도해도 같다
                    return []
                raise RuntimeError(f"{code} {msg}")
            root = next(iter(d))
            return d[root].get("row", [])
        except (urllib.error.URLError, TimeoutError, RuntimeError) as e:
            if n == tries - 1:
                raise
            time.sleep(1.5 * (n + 1))
    return []


# KOSIS 표도 분류(objL1) 차원이 있다. ALL 로 두면 원지수·순환변동치·
# 전월비 등 8종이 한 계열에 섞이므로 분류코드를 명시한다.
KOSIS = [
    ("sme_coincident", "중소기업 경기동행종합지수", "303", "DT_303005_CI001", "00", "2015=100",
     "통계청 KOSIS 303/DT_303005_CI001 중소기업 경기동행종합지수 · 동행종합지수"),
    ("sme_cycle", "중소기업 동행지수 순환변동치", "303", "DT_303005_CI001", "01", "2015=100",
     "통계청 KOSIS 303/DT_303005_CI001 · 동행지수 순환변동치"),
]


def fetch_kosis(key, org, tbl, obj, start, end, tries=3):
    q = urllib.parse.urlencode({
        "method": "getList", "apiKey": key, "format": "json", "jsonVD": "Y",
        "orgId": org, "tblId": tbl, "itmId": "ALL", "objL1": obj,
        "prdSe": "M", "startPrdDe": start, "endPrdDe": end,
    })
    url = f"https://kosis.kr/openapi/Param/statisticsParameterData.do?{q}"
    for n in range(tries):
        try:
            d = json.loads(urllib.request.urlopen(url, timeout=40).read().decode("utf-8"))
            if isinstance(d, dict):                 # 오류는 dict 로 온다
                raise RuntimeError(str(d)[:120])
            return d
        except Exception:
            if n == tries - 1:
                raise
            time.sleep(1.5 * (n + 1))
    return []


def main():
    key = read_key()
    out = {
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "한국은행 경제통계시스템(ECOS) 오픈API",
        "period": {"start": START, "end": END},
        "series": {},
    }

    for skey, label, stat, item, item2, unit, grp, direction, cite in SERIES:
        try:
            rows = fetch(key, stat, item, item2, "M", START, END)
        except Exception as e:
            print(f"  ✗ {label}: {type(e).__name__} {e}")
            continue

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

        # 2차원을 덜 지정해 같은 시점이 여러 번 들어오면 계열이 오염된다.
        times = [o["t"] for o in obs]
        if len(times) != len(set(times)):
            dup = len(times) - len(set(times))
            print(f"  ✗ {label}: 같은 시점이 {dup}건 중복 — 항목코드 지정이 부족합니다")
            continue

        out["series"][skey] = {
            "label": label,
            "stat_code": stat,
            "item_code": item,
            "item_code2": item2,
            "cycle": "M",
            "unit": unit,
            "group": grp,
            "risk_dir": direction,
            "citation": cite,
            "n": len(obs),
            "obs": obs,
        }
        span = f"{obs[0]['t']}~{obs[-1]['t']}" if obs else "없음"
        print(f"  ✓ {label:<20} {len(obs):>4}건  {span}")
        time.sleep(0.4)

    kkey = read_key("KOSIS_API_KEY", required=False)
    if kkey:
        for skey, label, org, tbl, obj, unit, cite in KOSIS:
            try:
                rows = fetch_kosis(kkey, org, tbl, obj, START, END)
            except Exception as e:
                print(f"  ✗ {label}: {type(e).__name__} {e}")
                continue
            obs = []
            for r in rows:
                v = (r.get("DT") or "").strip()
                if not v:
                    continue
                try:
                    obs.append({"t": r["PRD_DE"], "v": float(v)})
                except ValueError:
                    continue
            obs.sort(key=lambda o: o["t"])
            times = [o["t"] for o in obs]
            if len(times) != len(set(times)):
                print(f"  ✗ {label}: 같은 시점 중복 — 분류(objL1) 지정이 필요합니다")
                continue
            out["series"][skey] = {
                "label": label, "org_id": org, "tbl_id": tbl, "obj_l1": obj, "cycle": "M",
                "unit": unit, "citation": cite, "n": len(obs), "obs": obs,
            }
            span = f"{obs[0]['t']}~{obs[-1]['t']}" if obs else "없음"
            print(f"  ✓ {label:<20} {len(obs):>4}건  {span}")
    else:
        print("  · KOSIS_API_KEY 없음 — 통계청 계열은 건너뜁니다")

    out["source"] = "한국은행 ECOS / 통계청 KOSIS 오픈API"
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(s["n"] for s in out["series"].values())
    print(f"\n저장: {OUT.name}  계열 {len(out['series'])}개 / 관측치 {total}건")


if __name__ == "__main__":
    main()
