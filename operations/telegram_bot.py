"""
경제 1분 · 운영팀 — 텔레그램 결재 봇
1) 결재 요청: posts/ 에 새 게시물이 오면 카드 6장 + 릴스 + 캡션을 텔레그램으로 보내고 [✅ 승인] [❌ 거절] 버튼을 단다.
2) 결재 확인: 버튼이 눌렸는지 확인해서, 승인이면 인스타그램에 게시(post_instagram.publish), 거절이면 기록만 남긴다.

깃허브 Actions가 5분마다(그리고 새 게시물이 올라올 때마다) 실행한다:  python telegram_bot.py
비밀값: TELEGRAM_BOT_TOKEN (봇 열쇠), IG_ACCESS_TOKEN (인스타 열쇠)

보안: 운영자 채팅 ID는 operations/telegram_config.json 에 저장된다.
      처음 봇에게 /start 를 보낸 사람이 운영자로 등록되고, 그 뒤로는 운영자의 버튼만 인정한다.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import post_instagram as ig  # noqa: E402

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "telegram_config.json"   # {"chat_id": ..., "name": "..."}
STATE = HERE / "telegram_state.json"     # {"offset": 마지막으로 처리한 update_id}
POSTS = ig.POSTS
BOT = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
LOOP = int(os.environ.get("LOOP_SECONDS", "0") or 0)   # 0이면 한 번만 확인, 아니면 그 시간 동안 계속 대기
API = f"https://api.telegram.org/bot{BOT}"


def tg(method, files=None, **params):
    for k, v in list(params.items()):
        if isinstance(v, (dict, list)):
            params[k] = json.dumps(v, ensure_ascii=False)
    r = requests.post(f"{API}/{method}", data=params, files=files, timeout=180)
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"텔레그램 오류 ({method}): {body.get('description')}")
    return body["result"]


def answer(q, text):
    """버튼 응답 (오래된 버튼이면 텔레그램이 거부하므로 실패해도 넘어감)"""
    try:
        tg("answerCallbackQuery", callback_query_id=q["id"], text=text)
    except Exception as e:
        print(f"   (버튼 응답 생략: {e})")


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def owner():
    return load(CONFIG, {}).get("chat_id")


# ── 1) 결재 요청 보내기 ─────────────────────────────────────────
def needs_request(folder: Path):
    if not (folder / "post.json").exists() or any((folder / f).exists() for f in ("request.json", "rejected.json")):
        return False
    done = load(folder / "published.json", None)
    return done is None or bool(done.get("reel_error"))   # 릴스만 실패한 경우도 다시 결재 요청


def send_request(folder: Path, chat_id):
    post, images, reel = ig.load_post(folder)
    title = post.get("title", folder.name)
    handles = [open(p, "rb") for p in images]
    try:
        if images:
            media = [{"type": "photo", "media": f"attach://p{i}"} for i in range(len(images))]
            media[0]["caption"] = f"🖼 {title}"
            tg("sendMediaGroup", chat_id=chat_id, media=media,
               files={f"p{i}": (p.name, h, "image/jpeg") for i, (p, h) in enumerate(zip(images, handles))})
    finally:
        for h in handles:
            h.close()
    if reel:
        with open(reel, "rb") as v:
            tg("sendVideo", chat_id=chat_id, caption="🎬 릴스 미리보기", supports_streaming="true",
               files={"video": (reel.name, v, "video/mp4")})
    what = " + ".join(filter(None, [f"카드 {len(images)}장" if images else "", "릴스 1개" if reel else ""]))
    text = (f"📮 결재 요청: {title}\n\n{post.get('caption', '')}\n\n"
            f"──────────\n위 {what}를 econ.insta에 게시할까요?")[:4000]
    msg = tg("sendMessage", chat_id=chat_id, text=text, reply_markup={"inline_keyboard": [[
        {"text": "✅ 승인", "callback_data": f"ok:{folder.name}"},
        {"text": "❌ 거절", "callback_data": f"no:{folder.name}"}]]})
    save(folder / "request.json", {"message_id": msg["message_id"], "chat_id": chat_id, "sent_at": ig.now()})
    print(f"📨 결재 요청 보냄: {title}")


# ── 2) 버튼·메시지 확인 ─────────────────────────────────────────
def handle_message(m, cfg):
    chat, text = m["chat"], (m.get("text") or "").strip()
    if not text.startswith("/start"):
        return
    if not cfg.get("chat_id"):
        name = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or chat.get("username", "")
        cfg.update(chat_id=chat["id"], name=name)
        save(CONFIG, cfg)
        tg("sendMessage", chat_id=chat["id"], text=f"✅ 경제 1분 결재 봇에 운영자로 등록됐어요 ({name}).\n새 카드뉴스가 나오면 여기로 결재 요청을 보낼게요.")
        print(f"👤 운영자 등록: {name} ({chat['id']})")
    elif chat["id"] == cfg["chat_id"]:
        tg("sendMessage", chat_id=chat["id"], text="✅ 이미 연결돼 있어요. 결재 요청을 기다려 주세요.")
    else:
        tg("sendMessage", chat_id=chat["id"], text="이 봇은 운영자 전용이에요.")
        print(f"🚫 등록되지 않은 사용자의 /start 무시: {chat['id']}")


def handle_callback(q, cfg):
    data, who = q.get("data", ""), q["from"]["id"]
    msg = q.get("message") or {}
    if who != cfg.get("chat_id"):
        answer(q, "운영자만 누를 수 있어요.")
        print(f"🚫 권한 없는 버튼 무시: {who}")
        return
    action, _, name = data.partition(":")
    folder = POSTS / name
    if not name or "/" in name or ".." in name or not folder.is_dir():
        answer(q, "게시물을 찾을 수 없어요.")
        print(f"   ↳ 게시물 폴더 없음: {name}")
        return

    def update(text, keep_buttons=False):
        # 텔레그램은 글을 고치면 버튼이 사라지므로, 재시도가 필요할 땐 버튼을 다시 붙인다
        if msg:
            extra = {"reply_markup": msg["reply_markup"]} if keep_buttons and msg.get("reply_markup") else {}
            tg("editMessageText", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
               text=(msg.get("text", "") + "\n\n" + text)[:4000], **extra)

    if (folder / "rejected.json").exists() or ((folder / "published.json").exists()
                                              and not load(folder / "published.json", {}).get("reel_error")):
        answer(q, "이미 처리된 게시물이에요.")
        print(f"   ↳ 이미 처리된 게시물: {name}")
        return
    if action == "no":
        save(folder / "rejected.json", {"rejected_at": ig.now()})
        answer(q, "거절했어요.")
        update("❌ 거절됨 — 게시하지 않았어요.")
        print(f"❌ 거절: {name}")
        return
    if action != "ok":
        return
    answer(q, "승인! 게시를 시작해요 (몇 분 걸려요)")
    try:
        r = ig.publish(folder)
    except Exception as e:
        update(f"⚠️ 게시 실패: {str(e)[:300]}\n(다시 [✅ 승인]을 누르면 재시도해요)", keep_buttons=True)
        print(f"⚠️ 게시 실패: {name} — {e}")
        return
    lines = ["✅ 승인 완료"]
    if r.get("dry_run"):
        lines.append("🧪 시험 모드라 실제로는 올리지 않았어요 (인스타 열쇠 없음)")
    if r.get("permalink"):
        lines.append(f"🖼 카드뉴스: {r['permalink']}")
    if r.get("reel_permalink"):
        lines.append(f"🎬 릴스: {r['reel_permalink']}")
    if r.get("reel_error"):
        lines.append(f"⚠️ 릴스 실패: {r['reel_error'][:200]}\n(다시 [✅ 승인]을 누르면 릴스만 재시도해요)")
    if msg and not r.get("reel_error"):
        tg("editMessageReplyMarkup", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
           reply_markup={"inline_keyboard": []})
    update("\n".join(lines), keep_buttons=bool(r.get("reel_error")))
    print(f"✅ 처리: {name} → {r}")


def describe(u):
    if "callback_query" in u:
        q = u["callback_query"]
        return f"버튼 '{q.get('data')}' (누른 사람 {q['from']['id']})"
    if "message" in u:
        m = u["message"]
        return f"메시지 '{(m.get('text') or '')[:30]}' (보낸 사람 {m['chat']['id']})"
    return f"기타 {list(u)}"


def poll(wait=0):
    cfg, state = load(CONFIG, {}), load(STATE, {"offset": 0})
    updates = tg("getUpdates", offset=state["offset"] + 1, timeout=wait,
                 allowed_updates=["message", "callback_query"])
    for u in updates:
        print(f"📥 받음: {describe(u)}")
        try:
            if "message" in u:
                handle_message(u["message"], cfg)
            elif "callback_query" in u:
                handle_callback(u["callback_query"], cfg)
        except Exception as e:
            print(f"⚠️ 처리 중 오류 (update {u['update_id']}): {e}")
        state["offset"] = u["update_id"]
        save(STATE, state)   # 한 건씩 저장 → 같은 버튼을 두 번 처리하지 않음
    return cfg, bool(updates)


def git(*args):
    return subprocess.run(["git", *args], cwd=ig.ROOT, capture_output=True, text=True)


def sync():
    """기록(결재·게시 결과)을 깃허브에 저장하고, 디자인팀이 새로 올린 게시물을 받아온다."""
    if not LOOP:
        return
    for f in ("posts", "operations/telegram_config.json", "operations/telegram_state.json"):
        if (ig.ROOT / f).exists():
            git("add", f)
    if git("diff", "--cached", "--quiet").returncode:
        git("commit", "-q", "-m", "운영팀: 텔레그램 결재 기록")
    for _ in range(3):
        git("pull", "-q", "--rebase", "origin", "main")
        if git("push", "-q", "origin", "HEAD:main").returncode == 0:
            return
        time.sleep(5)
    print("⚠️ 기록 저장(push) 실패 — 다음 확인 때 다시 시도해요")


def send_pending(chat_id):
    if not POSTS.exists():
        return False
    sent = False
    for folder in sorted(p for p in POSTS.iterdir() if p.is_dir()):
        if needs_request(folder):
            try:
                send_request(folder, chat_id)
                sent = True
            except Exception as e:
                print(f"⚠️ 결재 요청 실패: {folder.name} — {e}")
    return sent


def main():
    if not BOT:
        print("TELEGRAM_BOT_TOKEN 비밀값이 없어서 텔레그램 결재를 건너뛰어요.")
        return
    me = tg("getMe")
    print(f"🤖 연결된 봇: @{me.get('username')} ({me.get('first_name')})"
          + (f" — {LOOP // 60}분 동안 대기하며 버튼을 바로 처리해요" if LOOP else ""))
    end = time.time() + LOOP
    last_pull = 0
    while True:
        cfg, got = poll(wait=20 if LOOP else 0)
        chat_id = cfg.get("chat_id")
        if not chat_id:
            print("ℹ️ 아직 운영자가 등록되지 않았어요. 텔레그램에서 봇에게 /start 를 보내주세요.")
        changed = got
        if LOOP and time.time() - last_pull > 60:   # 1분마다 디자인팀의 새 게시물 확인
            git("pull", "-q", "--rebase", "origin", "main")
            last_pull = time.time()
        if chat_id and send_pending(chat_id):
            changed = True
        if changed:
            sync()
        if time.time() >= end:
            break


if __name__ == "__main__":
    main()
