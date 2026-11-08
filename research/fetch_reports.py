"""
경제 1분 · 리서치팀
평일 아침 sources.json 에 적힌 기관들의 보고서 목록을 확인해서,
1) 처음 보는 보고서를 reports.md(사람용 목록)와 index.json(편집팀용 목록)에 추가하고
2) 최근 보고서와 에버그린 보고서(evergreen.json)의 원문(요약문·PDF 본문)을 docs/ 에 받아둔다.

기관마다 사이트 구조가 달라서 parser 이름으로 읽는 방법을 고른다:
  bok   한국은행 BOK 이슈노트      kdi   KDI FOCUS
  kiep  KIEP 세계경제 포커스        kcmi  자본시장연구원 이슈보고서
  mods  국가데이터처 보도자료 (주요 지표만, include 키워드로 거름)
"""
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).parent
SOURCES = HERE / "sources.json"
SEEN = HERE / "seen.json"            # 이미 본 보고서 기록 (중복 방지)
REPORTS = HERE / "reports.md"        # 사람이 보는 목록
INDEX = HERE / "index.json"          # 편집팀이 읽는 목록 (원문 파일 위치 포함)
EVERGREEN = HERE / "evergreen.json"  # 에버그린(시대를 타지 않는) 한국은행 보고서 목록
DOCS = HERE / "docs"
KST = timezone(timedelta(hours=9))
DATE_RE = re.compile(r"(20\d{2})[.\-/]\s?(\d{1,2})[.\-/]\s?(\d{1,2})")
HEADERS = {"User-Agent": "Mozilla/5.0 (econ-1min research bot)"}
DOCS_PER_SOURCE = 3        # 기관별로 한 번에 새로 받을 원문 수
EVERGREEN_PER_RUN = 3      # 에버그린 원문을 한 번에 받을 수
RECENT_DAYS = 30           # 이 기간 안에 나온 보고서만 원문을 받음
PDF_MAX_CHARS = 40000
TODAY = datetime.now(KST).date()


def text(el):
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def ymd(s):
    m = DATE_RE.search(s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def qs(url, name):
    return parse_qs(urlparse(url).query).get(name, [""])[0]


# ── 기관별 목록 읽기: [{key, title, url, date, pdf, summary}] ─────────────
def list_bok(soup, src):
    out = {}
    for a in soup.find_all("a", href=True):
        if "P0002353/view.do" not in a["href"]:
            continue
        url = urljoin(src["base_url"] + "/portal/bbs/", a["href"])
        nid = qs(url, "nttId")
        title = text(a)
        if not nid or len(title) < 4 or nid in out:
            continue
        box = a.find_parent(["li", "tr"]) or a.parent
        out[nid] = {"key": nid, "title": title, "date": ymd(text(box)),
                    "url": f"{src['base_url']}/portal/bbs/P0002353/view.do?nttId={nid}&menuNo=200433"}
    return list(out.values())


def list_kdi(soup, src):
    out = {}
    for a in soup.find_all("a", href=re.compile(r"focusView\?pub_no=\d+")):
        pub = re.search(r"pub_no=(\d+)", a["href"]).group(1)
        if pub in out:
            continue
        li = a.find_parent("li") or a.parent
        title = text(a.find("strong")) or re.sub(r"^KDI FOCUS\s*", "", text(a))
        pdf = ""
        for b in li.find_all(onclick=re.compile(r"/file/download")):
            pdf = urljoin(src["base_url"], re.search(r"'(/file/download[^']+)'", b["onclick"]).group(1))
        out[pub] = {"key": f"kdi-{pub}", "title": title, "date": "", "pdf": pdf,
                    "url": f"{src['base_url']}/research/focusView?pub_no={pub}"}
    return list(out.values())


def list_kiep(soup, src):
    out = {}
    for a in soup.find_all("a", href=re.compile(r"act=view.*list_no=\d+|list_no=\d+.*act=view")):
        if "bid=0004" not in a["href"]:
            continue
        no = re.search(r"list_no=(\d+)", a["href"]).group(1)
        title = text(a)
        if no in out or len(title) < 4:
            continue
        box = a.find_parent(["li", "tr"]) or a.parent
        out[no] = {"key": f"kiep-{no}", "title": title, "date": ymd(text(box)),
                   "url": f"{src['base_url']}/gallery.es?mid=a10102030000&bid=0004&act=view&list_no={no}",
                   "pdf": f"{src['base_url']}/galleryDownload.es?bid=0004&list_no={no}&seq=1"}
    return list(out.values())


def list_kcmi(soup, src):
    out = []
    for box in soup.select("div.list[id^=rsidx_]"):
        no = box["id"].split("_")[-1]
        title = text(box.select_one(".rpt_title strong"))
        down = box.select_one("a.down[href]")
        out.append({"key": f"kcmi-{no}", "title": title, "date": ymd(text(box.select_one(".rpt_title em"))),
                    "url": f"{src['base_url']}/report/report_view?report_no={no}",
                    "pdf": urljoin(src["base_url"], down["href"]) if down else "",
                    "summary": text(box.select_one("p.txt"))})
    return out


def list_mods(soup, src):
    include = src.get("include", [])
    out = []
    for a in soup.select("a.board_link[href]"):
        m = re.search(r"bid=(\w+)&(?:amp;)?act=view&(?:amp;)?list_no=(\d+)", a["href"])
        if not m:
            continue
        bid, no = m.groups()
        title = re.sub(r"^새글\s*", "", text(a))
        if include and not any(k in title for k in include):
            continue
        li = a.find_parent("li")
        pdf = ""
        for f in (li.find_all("a", href=re.compile(r"boardDownload\.es")) if li else []):
            if "pdf" in (text(f) + f.get("title", "")).lower():
                pdf = urljoin(src["base_url"], f["href"])
        out.append({"key": f"mods-{no}", "title": title, "date": ymd(text(li)), "pdf": pdf,
                    "url": f"{src['base_url']}/board.es?mid=a10301010000&bid={bid}&act=view&list_no={no}"})
    return out


PARSERS = {"bok": list_bok, "kdi": list_kdi, "kiep": list_kiep, "kcmi": list_kcmi, "mods": list_mods}


# ── 상세 페이지에서 요약문·PDF 주소·날짜 보충 ─────────────────────────
def fill_detail(session, src, item):
    """목록에 없던 날짜·PDF 주소·요약문을 상세 페이지에서 채운다."""
    p = src["parser"]
    if p in ("kiep", "kcmi", "mods") and item.get("date") and item.get("pdf"):
        return item
    r = session.get(item["url"], timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    if p == "bok":
        box = soup.select_one(".dbdata")
        if box:
            item["summary"] = "\n\n".join(t for t in (text(x) for x in box.find_all("p")) if t)
        for a in soup.find_all("a", href=True):
            if a["href"].lower().endswith(".pdf") and "/fileSrc/" in a["href"]:
                item["pdf"] = urljoin(src["base_url"], a["href"])
                break
    elif p == "kdi":
        item["date"] = item.get("date") or ymd(text(soup.select_one(".new_top-title .title span")))
        if not item.get("pdf"):
            m = re.search(r"'(/file/download\?atch_no=[^']+)'", r.text)
            item["pdf"] = urljoin(src["base_url"], m.group(1)) if m else ""
    else:
        item["date"] = item.get("date") or ymd(r.text)
        if not item.get("pdf"):
            m = re.search(r'href="([^"]*(?:galleryDownload|boardDownload|downloadw)[^"]*)"', r.text)
            item["pdf"] = urljoin(src["base_url"], m.group(1).replace("&amp;", "&")) if m else ""
    return item


def pdf_text(session, url):
    from pypdf import PdfReader
    r = session.get(url, timeout=90)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        return "(PDF가 아닌 파일이라 본문을 읽지 못함)"
    reader = PdfReader(BytesIO(r.content))
    pages = [f"--- p.{i} ---\n" + (pg.extract_text() or "").strip() for i, pg in enumerate(reader.pages, 1)]
    return "\n\n".join(pages)[:PDF_MAX_CHARS]


def save_doc(session, src, item):
    """요약문 + PDF 본문을 docs/<key>.md 로 저장."""
    fill_detail(session, src, item)
    body = ""
    if item.get("pdf"):
        try:
            body = pdf_text(session, item["pdf"])
        except Exception as e:
            body = f"(PDF 본문 추출 실패: {e})"
    DOCS.mkdir(exist_ok=True)
    (DOCS / f"{item['key']}.md").write_text(
        f"# {item['title']}\n\n"
        f"- 기관: {src['name']}\n- 발간일: {item.get('date', '')}\n- 상세: {item['url']}\n- PDF: {item.get('pdf') or '없음'}\n\n"
        f"## 기관 요약문\n\n{item.get('summary') or '(요약문 없음 — PDF 본문 참고)'}\n\n"
        f"## PDF 본문 (앞부분 최대 {PDF_MAX_CHARS:,}자)\n\n{body or '(없음)'}\n",
        encoding="utf-8")


def recent(date_str, days=RECENT_DAYS):
    try:
        return (TODAY - datetime.strptime(date_str, "%Y-%m-%d").date()).days <= days
    except ValueError:
        return True   # 날짜를 모르면 일단 받아둔다


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def session_for(src):
    s = requests.Session()
    s.headers.update(HEADERS)
    if src["parser"] == "bok":   # 한국은행은 첫 화면 쿠키가 있어야 목록을 줌
        s.get(src["base_url"] + "/portal/main/main.do", timeout=30)
        s.headers.update({"Referer": src["base_url"] + "/portal/singl/newsData/list.do?menuNo=200433",
                          "X-Requested-With": "XMLHttpRequest"})
    return s


def main():
    sources = load_json(SOURCES, [])
    seen = load_json(SEEN, {})
    index = load_json(INDEX, {})
    today = TODAY.isoformat()
    new_lines, problems = [], []

    for src in sources:
        try:
            s = session_for(src)
            r = s.get(src["list_url"], timeout=30)
            r.raise_for_status()
            r.encoding = "utf-8"
            items = PARSERS[src["parser"]](BeautifulSoup(r.text, "html.parser"), src)
        except Exception as e:  # 한 기관이 실패해도 나머지는 계속
            problems.append(f"{src['name']}: {e}")
            continue
        if not items:
            problems.append(f"{src['name']}: 목록에서 보고서를 하나도 못 찾음 (응답 {len(r.text)}자)")
            continue

        first_time = src["name"] not in seen
        bucket = seen.setdefault(src["name"], [])
        fetched = 0
        for it in items:
            rec = index.get(it["key"], {})
            is_new = it["key"] not in bucket
            # 원문 받기: 새로 봤거나 아직 원문이 없는 최근 보고서
            if fetched < DOCS_PER_SOURCE and not (DOCS / f"{it['key']}.md").exists() and \
                    recent(it.get("date") or rec.get("date", "")):
                try:
                    save_doc(s, src, it)
                    fetched += 1
                    print(f"📄 원문 저장: [{src['name']}] {it['title']}")
                except Exception as e:
                    print(f"⚠️ 원문 저장 실패 ({it['title']}): {e}")
            elif not it.get("date") and src["parser"] == "kdi" and is_new:
                try:
                    fill_detail(s, src, it)   # KDI 목록에는 날짜가 없어서 상세에서 확인
                except Exception:
                    pass
            date = it.get("date") or rec.get("date") or today
            index[it["key"]] = {"source": src["name"], "title": it["title"], "date": date, "url": it["url"],
                                "doc": f"docs/{it['key']}.md" if (DOCS / f"{it['key']}.md").exists() else None,
                                "evergreen": rec.get("evergreen", False)}
            if is_new:
                bucket.append(it["key"])
                safe = it["title"].replace("|", "/").replace("[", "\\[").replace("]", "\\]")
                new_lines.append(f"| {date} | {src['name']} | [{safe}]({it['url']}) | 대기 |")
        if first_time:
            print(f"ℹ️ {src['name']}: 처음 확인 — 기존 목록 {len(items)}건을 등록")

    # 에버그린 한국은행 보고서 원문 받기
    bok = next((x for x in sources if x["parser"] == "bok"), None)
    if bok:
        s, done = session_for(bok), 0
        for ev in load_json(EVERGREEN, []):
            key = ev["key"]
            item = {"key": key, "title": ev["title"], "date": ev["date"],
                    "url": f"{bok['base_url']}/portal/bbs/P0002353/view.do?nttId={key}&menuNo=200433"}
            if not (DOCS / f"{key}.md").exists() and done < EVERGREEN_PER_RUN:
                try:
                    save_doc(s, bok, item)
                    done += 1
                    print(f"🌲 에버그린 원문 저장: {ev['title']}")
                except Exception as e:
                    print(f"⚠️ 에버그린 원문 실패 ({ev['title']}): {e}")
            index[key] = {"source": bok["name"], "title": ev["title"], "date": ev["date"], "url": item["url"],
                          "doc": f"docs/{key}.md" if (DOCS / f"{key}.md").exists() else None,
                          "evergreen": True, "why": ev.get("why", "")}

    SEEN.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    INDEX.write_text(json.dumps(dict(sorted(index.items(), key=lambda kv: kv[1]["date"], reverse=True)),
                                ensure_ascii=False, indent=1), encoding="utf-8")

    header = "# 리서치팀 보고서 목록\n\n새 보고서가 위에 쌓입니다. 상태는 직접 고쳐 쓰세요 (대기 → 제작중 → 게시).\n\n| 발간일 | 기관 | 제목 | 상태 |\n|---|---|---|---|\n"
    old_rows = ""
    if REPORTS.exists():
        t = REPORTS.read_text(encoding="utf-8")
        old_rows = t.split("|---|---|---|---|\n", 1)[-1] if "|---|---|---|---|" in t else ""
    new_lines.sort(reverse=True)
    REPORTS.write_text(header + "\n".join(new_lines) + ("\n" if new_lines else "") + old_rows, encoding="utf-8")

    print(f"[{today}] 새 보고서 {len(new_lines)}건")
    for p in problems:
        print("⚠️", p)
    if problems and len(problems) == len(sources):
        sys.exit(1)


if __name__ == "__main__":
    main()
