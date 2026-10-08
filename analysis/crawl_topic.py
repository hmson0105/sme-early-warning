"""중소기업 위기 관련 뉴스 크롤링 + 토픽모델링 파이프라인"""
import urllib.request, urllib.parse, xml.etree.ElementTree as ET
import re, json, time, collections, datetime

QUERIES = [
    "중소기업 위기", "중소기업 부도", "중소기업 자금난", "중소기업 대출 연체",
    "소상공인 폐업", "중소기업 경영난", "기업 구조조정 중소기업", "중소기업 유동성",
    "중소기업 수출 부진", "협력업체 대금 미지급",
]

def fetch(query):
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    data = urllib.request.urlopen(req, timeout=25).read()
    root = ET.fromstring(data)
    out = []
    for it in root.findall('.//item'):
        title = (it.findtext('title') or '').strip()
        desc = re.sub(r'<[^>]+>', ' ', it.findtext('description') or '')
        pub = it.findtext('pubDate') or ''
        src = it.find('source')
        out.append({
            'title': title, 'desc': desc.strip(), 'pub': pub,
            'source': (src.text if src is not None else ''), 'query': query,
        })
    return out

docs = {}
for q in QUERIES:
    try:
        for a in fetch(q):
            key = re.sub(r'\s+', '', a['title'])[:60]
            if key and key not in docs:
                docs[key] = a
        print(f"  {q}: 누적 {len(docs)}건")
    except Exception as e:
        print(f"  {q}: ERROR {e}")
    time.sleep(0.6)

articles = list(docs.values())
print(f"\n총 수집(중복 제거): {len(articles)}건")

# ---------- 한국어 명사 추출 (정규식 기반) ----------
JOSA = ('으로서','으로써','에서는','에게서','이라는','라는','에서','에게','으로','까지','부터','보다',
        '와의','과의','에는','에도','이나','거나','만큼','처럼','대로','하고','이라','라고','도록',
        '은','는','이','가','을','를','의','에','와','과','도','만','로','께','랑','뿐','든','나')
STOP = set("""기사 뉴스 사진 영상 단독 속보 종합 인터뷰 기자 오늘 내일 어제 올해 지난해 내년 이번 관련 위해 통해
대한 대해 따라 대비 가운데 이번주 지역 우리 사람 경우 상황 문제 필요 가능 진행 개최 실시 추진 마련 확대 지원
제공 발표 공개 시작 개월 최근 당시 현재 이후 이전 동안 정도 수준 중소 기업 중소기업 소상공인 뉴시스 연합뉴스
머니투데이 아시아경제 서울경제 파이낸셜뉴스 전자신문 매일경제 한국경제 이데일리 뉴스핌 헤럴드경제 데일리 일보
신문 저널 미디어 방송 경제 뉴스1 조선일보 중앙일보 동아일보 국민일보 세계일보 한겨레 경향신문 news
머니투데 만에 앞두고 위한 해소 개최 만원 우려 규모 지난 이상 이하 대표 회장 사장 장관 위원장 데일리 투데 투데이
일간 코리아 타임즈 이코노미 비즈 매경 한경 조선비즈 아주 전북 경기 충북 강원 대구 부산 광주 인천 울산 대전 제주
세종 경남 경북 전남 충남 서울 지자체 센터 협회 공사 공단 재단 사업 정책 방안 계획 결과 확보 강화 개선 운영 조성
구축 도입 시행 발굴 육성 모집 신청 접수 선정 참여 협약 체결 간담회 세미나 포럼 국내 국제 글로벌 코로나 정부""".split())

def nouns(text):
    text = re.sub(r'\([^)]*\)', ' ', text)
    toks = re.findall(r'[가-힣]{2,}', text)
    out = []
    for t in toks:
        for j in JOSA:
            if len(t) > len(j) + 1 and t.endswith(j):
                t = t[:-len(j)]
                break
        if len(t) >= 2 and t not in STOP:
            out.append(t)
    return out

corpus_tokens = [nouns(a['title'] + ' ' + a['desc']) for a in articles]
corpus = [' '.join(t) for t in corpus_tokens]

# ---------- 키워드 빈도 ----------
freq = collections.Counter(w for toks in corpus_tokens for w in toks)
print("\n[상위 키워드 30]")
for w, c in freq.most_common(30):
    print(f"  {w}: {c}")

# ---------- LDA 토픽모델링 ----------
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.decomposition import LatentDirichletAllocation

vec = CountVectorizer(max_df=0.45, min_df=4, token_pattern=r'\S+')
X = vec.fit_transform(corpus)
vocab = vec.get_feature_names_out()
print(f"\n문서-단어 행렬: {X.shape}")

N_TOPICS = 5
lda = LatentDirichletAllocation(n_components=N_TOPICS, random_state=42,
                                learning_method='batch', max_iter=60)
W = lda.fit_transform(X)
assign = W.argmax(axis=1)

topics = []
for k, comp in enumerate(lda.components_):
    idx = comp.argsort()[::-1][:12]
    assigned = int((assign == k).sum())
    topics.append({'id': k, 'keywords': [vocab[i] for i in idx],
                   'weights': [round(float(comp[i]), 1) for i in idx],
                   'n_docs': assigned})
    print(f"\n[토픽 {k+1}] 문서 {assigned}건")
    print("  ", ', '.join(topics[-1]['keywords'][:10]))

# ---------- 월별 분포 ----------
def month(pub):
    try:
        return datetime.datetime.strptime(pub[:16].strip(), '%a, %d %b %Y').strftime('%Y-%m')
    except Exception:
        return None

mlist = [month(a['pub']) for a in articles]
months = collections.Counter(m for m in mlist if m)
print("\n[월별 기사 수 · 2026]")
for m, c in sorted(months.items()):
    if m >= '2026-01':
        print(f"  {m}: {c}")

tm = collections.defaultdict(lambda: [0] * N_TOPICS)
for m, k in zip(mlist, assign):
    if m and m >= '2026-01':
        tm[m][int(k)] += 1
month_topic = sorted(tm.items())

reps = {}
for k in range(N_TOPICS):
    cand = [i for i in range(len(articles)) if assign[i] == k]
    best = max(cand, key=lambda i: W[i][k]) if cand else None
    reps[k] = articles[best]['title'] if best is not None else ''

json.dump({
    'n_articles': len(articles),
    'n_queries': len(QUERIES),
    'vocab_size': int(X.shape[1]),
    'top_keywords': freq.most_common(30),
    'topics': topics,
    'months': sorted(months.items()),
    'month_topic': month_topic,
    'reps': reps,
    'sources': collections.Counter(a['source'] for a in articles).most_common(12),
}, open('topic_result.json', 'w'), ensure_ascii=False, indent=1)
print("\n저장: topic_result.json")
