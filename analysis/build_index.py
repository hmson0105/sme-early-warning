"""수집된 지표 풀로 상관분석과 종합 위기지수를 계산한다.

실행:  python3 analysis/build_index.py   (collect_ecos.py 실행 후)
산출:  data/ecos.json  — 대시보드가 읽는 최종 파일

하는 일
  1) 각 지표를 전년동월대비 변화로 바꾼다. 수준(level)끼리 상관을 재면
     둘 다 시간에 따라 흐른다는 이유만으로 높게 나오므로(허위상관),
     추세를 제거한 뒤 비교한다.
  2) 기준지표(기업대출 연체율)와의 교차상관을 시차 0~6개월로 계산해
     어떤 지표가 얼마나 앞서 움직이는지 본다.
  3) 방향(risk_dir)을 맞춰 표준화한 뒤 평균해 종합 위기지수를 만든다.
"""
import json, math, pathlib
from datetime import datetime

HERE = pathlib.Path(__file__).parent
SRC = HERE / "ecos_result.json"
OUT = HERE.parent / "data" / "ecos.json"

BASE = "delinq_corp"          # 기준지표: 기업대출 연체율
MAX_LAG = 6                   # 최대 6개월 선행까지 탐색


def yoy(obs):
    """전년동월대비 변화. 지수·금액은 %, 비율(%)은 %p 차이."""
    m = {o["t"]: o["v"] for o in obs}
    out = {}
    for t, v in m.items():
        y, mo = int(t[:4]), t[4:]
        prev = m.get(f"{y-1}{mo}")
        if prev is None:
            continue
        out[t] = (v / prev - 1) * 100 if abs(prev) > 1e-9 and abs(prev) > 5 else v - prev
    return out


def pearson(xs, ys):
    n = len(xs)
    if n < 12:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx < 1e-12 or sy < 1e-12:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def shift(t, k):
    """t 에서 k개월 뒤의 YYYYMM."""
    y, m = int(t[:4]), int(t[4:])
    m += k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y}{m:02d}"


def main():
    d = json.loads(SRC.read_text(encoding="utf-8"))
    S = d["series"]
    if BASE not in S:
        raise SystemExit(f"기준지표 {BASE} 가 없습니다. collect_ecos.py 를 먼저 실행하세요.")

    chg = {k: yoy(v["obs"]) for k, v in S.items()}
    base = chg[BASE]

    # ── 1) 교차상관: 지표가 k개월 선행할 때의 상관 ──
    corr = []
    for k, v in S.items():
        if k == BASE:
            continue
        best = None
        per_lag = []
        for lag in range(0, MAX_LAG + 1):
            xs, ys = [], []
            for t, cv in chg[k].items():
                bt = shift(t, lag)          # 지표(t) → 연체율(t+lag)
                if bt in base:
                    xs.append(cv)
                    ys.append(base[bt])
            r = pearson(xs, ys)
            per_lag.append({"lag": lag, "r": None if r is None else round(r, 3), "n": len(xs)})
            if r is not None and (best is None or abs(r) > abs(best["r"])):
                best = {"lag": lag, "r": round(r, 3), "n": len(xs)}
        if best:
            corr.append({
                "key": k, "label": v["label"], "group": v.get("group", ""),
                "unit": v["unit"], "dir": v.get("risk_dir", 0),
                "best_lag": best["lag"], "best_r": best["r"], "n": best["n"],
                "by_lag": per_lag,
            })
    corr.sort(key=lambda c: -abs(c["best_r"]))

    # ── 2) 종합 위기지수: 방향 정렬 후 z-score 평균 ──
    months = sorted({t for k in S for t in chg.get(k, {})})
    stats = {}
    for k in S:
        vals = list(chg.get(k, {}).values())
        if len(vals) < 12:
            continue
        mu = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals))
        if sd > 1e-9:
            stats[k] = (mu, sd)

    composite = []
    for t in months:
        zs = []
        for k, (mu, sd) in stats.items():
            v = chg[k].get(t)
            if v is None:
                continue
            zs.append(((v - mu) / sd) * S[k].get("risk_dir", 0))
        if len(zs) >= 10:
            composite.append({"t": t, "v": round(sum(zs) / len(zs), 3), "n": len(zs)})

    # 최근값의 경보 단계
    cur = composite[-1]["v"] if composite else 0.0
    level = ("위험" if cur >= 1.0 else "경계" if cur >= 0.5 else
             "주의" if cur >= 0.0 else "안정")

    d["analysis"] = {
        "base": BASE,
        "base_label": S[BASE]["label"],
        "method": ("전년동월대비 변화로 추세를 제거한 뒤 피어슨 상관을 계산했다. "
                   "시차 0~6개월을 모두 시도해 상관이 가장 큰 시차를 보고한다."),
        "correlation": corr,
        "composite": composite,
        "current": {"value": cur, "level": level, "t": composite[-1]["t"] if composite else None},
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"지표 {len(S)}개 · 종합지수 {len(composite)}개월")
    print(f"현재 {d['analysis']['current']['t']}  지수 {cur:+.2f}  경보 '{level}'\n")
    print("연체율과 상관이 큰 지표 (시차 = 지표가 앞서는 개월)")
    for c in corr[:10]:
        print(f"  r={c['best_r']:+.3f}  lag={c['best_lag']}개월  {c['label']}  [{c['group']}]")


if __name__ == "__main__":
    main()
