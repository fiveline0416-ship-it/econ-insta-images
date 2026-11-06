"""
경제 1분 · 리서치팀
매일 아침 sources.json에 적힌 기관 목록 페이지를 확인해서,
처음 보는 보고서를 reports.md 맨 위에 추가한다.
"""
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).parent
SOURCES = HERE / "sources.json"
SEEN = HERE / "seen.json"          # 이미 본 보고서 기록 (중복 방지)
REPORTS = HERE / "reports.md"      # 사람이 보는 목록
KST = timezone(timedelta(hours=9))
DATE_RE = re.compile(r"(20\d{2})[.\-/]\s?(\d{1,2})[.\-/]\s?(\d{1,2})")
HEADERS = {"User-Agent": "Mozilla/5.0 (econ-1min research bot)"}
DOCS = HERE / "docs"               # 보고서별 원문 요약 + PDF 본문 (편집팀이 읽음)
DOCS_PER_RUN = 5                   # 한 번에 새로 받아올 원문 수 (서버 부담 줄이기)
PDF_MAX_CHARS = 40000


def save_doc(session, src, item):
    """보고서 상세 페이지의 요약문과 PDF 본문을 docs/<번호>.md 로 저장."""
    from io import BytesIO
    r = session.get(item["url"], timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    box = soup.select_one(".dbdata")
    summary = ""
    if box:
        paras = [" ".join(p.get_text(" ", strip=True).split()) for p in box.find_all("p")]
        summary = "\n\n".join(p for p in paras if p)
    pdf_url, pdf_text = "", ""
    for a in soup.find_all("a", href=True):
        if a["href"].lower().endswith(".pdf") and "/fileSrc/" in a["href"]:
            pdf_url = urljoin(src["base_url"], a["href"])
            break
    if pdf_url:
        try:
            from pypdf import PdfReader
            pr = session.get(pdf_url, timeout=60)
            pr.raise_for_status()
            reader = PdfReader(BytesIO(pr.content))
            pages = []
            for i, page in enumerate(reader.pages, 1):
                pages.append(f"--- p.{i} ---\n" + (page.extract_text() or "").strip())
            pdf_text = "\n\n".join(pages)[:PDF_MAX_CHARS]
        except Exception as e:
            pdf_text = f"(PDF 본문 추출 실패: {e})"
    DOCS.mkdir(exist_ok=True)
    (DOCS / f"{item['key']}.md").write_text(
        f"# {item['title']}\n\n"
        f"- 기관: {src['name']}\n- 발간일: {item['date']}\n- 상세: {item['url']}\n- PDF: {pdf_url or '없음'}\n\n"
        f"## 한국은행 요약문\n\n{summary or '(요약문 없음)'}\n\n"
        f"## PDF 본문 (앞부분 최대 {PDF_MAX_CHARS:,}자)\n\n{pdf_text or '(없음)'}\n",
        encoding="utf-8")


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def item_key(url):
    """게시물 고유번호(nttId)가 있으면 그걸, 없으면 주소 자체를 키로 쓴다."""
    q = parse_qs(urlparse(url).query)
    return q.get("nttId", [url])[0]


def parse_list(html, source):
    """목록 페이지에서 (제목, 링크, 날짜) 뽑기."""
    soup = BeautifulSoup(html, "html.parser")
    found, keys = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if source["link_contains"] not in href:
            continue
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 4:
            continue
        url = urljoin(source["base_url"] + "/portal/bbs/", href)
        key = item_key(url)
        q = parse_qs(urlparse(url).query)
        if "nttId" in q:  # 긴 주소를 짧게 정리
            menu = q.get("menuNo", [""])[0]
            url = url.split("?")[0] + f"?nttId={q['nttId'][0]}" + (f"&menuNo={menu}" if menu else "")
        if key in keys:
            continue
        keys.add(key)
        # 날짜는 링크를 감싼 줄(li/tr/div)에서 찾는다
        date = ""
        box = a.find_parent(["li", "tr"]) or a.parent
        m = DATE_RE.search(box.get_text(" ", strip=True)) if box else None
        if m:
            date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        found.append({"key": key, "title": title, "url": url, "date": date})
    return found


def main():
    sources = load_json(SOURCES, [])
    seen = load_json(SEEN, {})
    first_run = not SEEN.exists()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    new_lines, problems = [], []

    for src in sources:
        try:
            s = requests.Session()
            s.headers.update(HEADERS)
            # 사람이 들어가는 것처럼 첫 화면을 한 번 열어 쿠키를 받은 뒤 목록을 요청
            s.get(src["base_url"] + "/portal/main/main.do", timeout=30)
            r = s.get(src["list_url"], timeout=30, headers={
                "Referer": src["base_url"] + "/portal/singl/newsData/list.do?menuNo=200433",
                "X-Requested-With": "XMLHttpRequest",
            })
            r.raise_for_status()
            r.encoding = "utf-8"
            items = parse_list(r.text, src)
        except Exception as e:  # 한 기관이 실패해도 나머지는 계속
            problems.append(f"{src['name']}: {e}")
            continue

        if not items:
            # 원인 파악용 진단 정보
            body = " ".join(r.text.split())
            print("── 진단 ──")
            print("요청 주소:", src["list_url"])
            print("응답 코드:", r.status_code, "/ 길이:", len(r.text), "/ 최종 주소:", r.url)
            print("앞부분:", body[:600])
            problems.append(f"{src['name']}: 목록에서 보고서를 하나도 못 찾음")
            continue

        bucket = seen.setdefault(src["name"], [])
        for it in items:
            if it["key"] in bucket:
                continue
            bucket.append(it["key"])
            date = it["date"] or today
            safe = it["title"].replace("|", "/").replace("[", "\\[").replace("]", "\\]")
            new_lines.append(f"| {date} | {src['name']} | [{safe}]({it['url']}) | 대기 |")

        # 원문(요약문·PDF) 아직 안 받은 보고서는 최신순으로 몇 건씩 받아두기
        fetched = 0
        for it in items:
            if fetched >= DOCS_PER_RUN or (DOCS / f"{it['key']}.md").exists():
                continue
            try:
                save_doc(s, src, it)
                fetched += 1
                print(f"📄 원문 저장: {it['title']}")
            except Exception as e:
                print(f"⚠️ 원문 저장 실패 ({it['title']}): {e}")

    SEEN.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")

    header = "# 리서치팀 보고서 목록\n\n새 보고서가 위에 쌓입니다. 상태는 직접 고쳐 쓰세요 (대기 → 제작중 → 게시).\n\n| 발간일 | 기관 | 제목 | 상태 |\n|---|---|---|---|\n"
    old_rows = ""
    if REPORTS.exists():
        text = REPORTS.read_text(encoding="utf-8")
        old_rows = text.split("|---|---|---|---|\n", 1)[-1] if "|---|---|---|---|" in text else ""
    new_lines.sort(reverse=True)
    REPORTS.write_text(header + "\n".join(new_lines) + ("\n" if new_lines else "") + old_rows, encoding="utf-8")

    label = "첫 실행: 기존 목록 저장" if first_run else "새 보고서"
    print(f"[{today}] {label} {len(new_lines)}건")
    for p in problems:
        print("⚠️", p)
    # 모든 기관이 실패했을 때만 오류로 표시 (깃허브 Actions 탭에 빨간불)
    if problems and len(problems) == len(sources):
        sys.exit(1)


if __name__ == "__main__":
    main()
