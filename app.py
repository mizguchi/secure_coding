import eventlet
eventlet.monkey_patch()

import os
import time
import sqlite3
from datetime import timedelta
from collections import defaultdict
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from flask_socketio import SocketIO, emit, join_room
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# ── 1. Secret Key: 파일에서 읽거나 최초 1회 랜덤 생성 ──────────────────────────
SECRET_KEY_FILE = os.path.join(os.path.dirname(__file__), 'secret.key')
if os.path.exists(SECRET_KEY_FILE):
    with open(SECRET_KEY_FILE, 'rb') as f:
        app.secret_key = f.read()
else:
    key = os.urandom(32)
    with open(SECRET_KEY_FILE, 'wb') as f:
        f.write(key)
    app.secret_key = key

# ── 2. CSRF 보호 ───────────────────────────────────────────────────────────────
csrf = CSRFProtect(app)

# ── 5. 세션 쿠키 보안 설정 ────────────────────────────────────────────────────
app.config['SESSION_COOKIE_HTTPONLY'] = True    # JS에서 쿠키 접근 차단
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF 방어 보조
# app.config['SESSION_COOKIE_SECURE'] = True    # HTTPS 환경에서만 활성화

# ── 6. 세션 만료 시간 설정 (2시간) ───────────────────────────────────────────
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)

socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')
DATABASE = os.path.join(os.path.dirname(__file__), 'market.db')
REPORT_THRESHOLD = 3

# ── 3. 로그인 시도 제한 (IP당 5분에 5회) ──────────────────────────────────────
_login_attempts: dict = defaultdict(list)

def is_rate_limited(ip: str) -> bool:
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < 300]
    if len(_login_attempts[ip]) >= 5:
        return True
    _login_attempts[ip].append(now)
    return False

# ── 4. 이미지 URL 검증 ─────────────────────────────────────────────────────────
def is_valid_url(url: str) -> bool:
    if not url:
        return True
    return url.startswith('http://') or url.startswith('https://')

# ── 7. 채팅 Rate Limiting (유저당 10초에 5개) ─────────────────────────────────
_chat_attempts: dict = defaultdict(list)

def is_chat_rate_limited(uid: int) -> bool:
    now = time.time()
    _chat_attempts[uid] = [t for t in _chat_attempts[uid] if now - t < 10]
    if len(_chat_attempts[uid]) >= 5:
        return True
    _chat_attempts[uid].append(now)
    return False


# DB 헬퍼
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT UNIQUE NOT NULL,
                password  TEXT NOT NULL,
                bio       TEXT,
                balance   INTEGER DEFAULT 50000,
                is_active INTEGER DEFAULT 1,
                is_admin  INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT,
                price       INTEGER NOT NULL,
                image_url   TEXT,
                seller_id   INTEGER NOT NULL,
                is_sold     INTEGER DEFAULT 0,
                is_blocked  INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER NOT NULL,
                target_type TEXT NOT NULL,
                target_id   INTEGER NOT NULL,
                reason      TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(reporter_id, target_type, target_id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id  INTEGER NOT NULL,
                room       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id   INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                amount      INTEGER NOT NULL,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS wishlists (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(user_id, product_id)
            );
        """)
        if not db.execute("SELECT id FROM users WHERE username='admin'").fetchone():
            db.execute(
                "INSERT INTO users (username, password, is_admin) VALUES (?,?,1)",
                ('admin', generate_password_hash('Admin@secure99!'))
            )
        db.commit()


# 인증 헬퍼
def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    return get_db().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            flash('로그인이 필요합니다.')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        u = current_user()
        if not u or not u['is_admin']:
            flash('관리자 권한이 필요합니다.')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def apply_report_action(db, target_type, target_id):
    count = db.execute(
        'SELECT COUNT(*) FROM reports WHERE target_type=? AND target_id=?',
        (target_type, target_id)
    ).fetchone()[0]
    if count >= REPORT_THRESHOLD:
        if target_type == 'product':
            db.execute('UPDATE products SET is_blocked=1 WHERE id=?', (target_id,))
        elif target_type == 'user':
            db.execute('UPDATE users SET is_active=0 WHERE id=?', (target_id,))
        db.commit()
        return True
    return False


def dm_room(id1, id2):
    a, b = sorted([id1, id2])
    return f'dm_{a}_{b}'


# 라우트

@app.route('/')
def index():
    db = get_db()
    q = request.args.get('q', '').strip()
    if q:
        rows = db.execute(
            """SELECT p.*, u.username AS seller_name
               FROM products p JOIN users u ON p.seller_id=u.id
               WHERE p.is_blocked=0 AND p.is_sold=0
                 AND (p.name LIKE ? OR p.description LIKE ?)
               ORDER BY p.created_at DESC""",
            (f'%{q}%', f'%{q}%')
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT p.*, u.username AS seller_name
               FROM products p JOIN users u ON p.seller_id=u.id
               WHERE p.is_blocked=0 AND p.is_sold=0
               ORDER BY p.created_at DESC"""
        ).fetchall()
    return render_template('index.html', products=rows, q=q, user=current_user())


@app.route('/products/new', methods=['GET', 'POST'])
@login_required
def add_product():
    if request.method == 'POST':
        name  = request.form.get('name', '').strip()
        price = request.form.get('price', '0')
        desc  = request.form.get('description', '').strip()
        img   = request.form.get('image_url', '').strip()
        if not name or not price.isdigit():
            flash('상품명과 가격을 올바르게 입력하세요.')
            return redirect(url_for('add_product'))
        # ── 이미지 URL 검증 ──
        if not is_valid_url(img):
            flash('이미지 URL은 http:// 또는 https://로 시작해야 합니다.')
            return redirect(url_for('add_product'))
        db = get_db()
        db.execute(
            'INSERT INTO products (name, description, price, image_url, seller_id) VALUES (?,?,?,?,?)',
            (name, desc, int(price), img, session['user_id'])
        )
        db.commit()
        flash('상품이 등록되었습니다.')
        return redirect(url_for('index'))
    return render_template('add_product.html', user=current_user())


@app.route('/products/<int:pid>')
def product_detail(pid):
    db = get_db()
    product = db.execute(
        """SELECT p.*, u.username AS seller_name
           FROM products p JOIN users u ON p.seller_id=u.id
           WHERE p.id=?""", (pid,)
    ).fetchone()
    if not product:
        flash('상품을 찾을 수 없습니다.')
        return redirect(url_for('index'))
    u = current_user()
    wished = False
    if u:
        wished = bool(db.execute(
            'SELECT id FROM wishlists WHERE user_id=? AND product_id=?',
            (u['id'], pid)
        ).fetchone())
    return render_template('product_detail.html', product=product, user=u, wished=wished)


@app.route('/products/<int:pid>/wish', methods=['POST'])
@login_required
def toggle_wish(pid):
    db = get_db()
    uid = session['user_id']
    existing = db.execute(
        'SELECT id FROM wishlists WHERE user_id=? AND product_id=?', (uid, pid)
    ).fetchone()
    if existing:
        db.execute('DELETE FROM wishlists WHERE user_id=? AND product_id=?', (uid, pid))
        flash('찜 목록에서 제거되었습니다.')
    else:
        db.execute('INSERT INTO wishlists (user_id, product_id) VALUES (?,?)', (uid, pid))
        flash('찜 목록에 추가되었습니다.')
    db.commit()
    return redirect(url_for('product_detail', pid=pid))


@app.route('/products/<int:pid>/toggle_sold', methods=['POST'])
@login_required
def toggle_sold(pid):
    db = get_db()
    p = db.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()
    if p and p['seller_id'] == session['user_id']:
        db.execute('UPDATE products SET is_sold=? WHERE id=?', (0 if p['is_sold'] else 1, pid))
        db.commit()
    return redirect(url_for('product_detail', pid=pid))


@app.route('/products/<int:pid>/delete', methods=['POST'])
@login_required
def delete_product(pid):
    db = get_db()
    p = db.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()
    if p and p['seller_id'] == session['user_id']:
        db.execute('DELETE FROM products WHERE id=?', (pid,))
        db.commit()
        flash('상품이 삭제되었습니다.')
    return redirect(url_for('index'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            flash('아이디와 비밀번호를 입력하세요.')
            return redirect(url_for('register'))
        # ── 비밀번호 길이 검증 ──
        if len(password) < 4:
            flash('비밀번호는 4자 이상이어야 합니다.')
            return redirect(url_for('register'))
        db = get_db()
        if db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone():
            flash('이미 사용 중인 아이디입니다.')
            return redirect(url_for('register'))
        db.execute(
            'INSERT INTO users (username, password) VALUES (?,?)',
            (username, generate_password_hash(password))
        )
        db.commit()
        flash('회원가입이 완료되었습니다. 로그인하세요.')
        return redirect(url_for('login'))
    return render_template('register.html', user=current_user())


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # ── 로그인 시도 횟수 제한 ──
        ip = request.remote_addr or '0.0.0.0'
        if is_rate_limited(ip):
            flash('로그인 시도가 너무 많습니다. 5분 후 다시 시도하세요.')
            return redirect(url_for('login'))

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        u = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if not u or not check_password_hash(u['password'], password):
            flash('아이디 또는 비밀번호가 틀렸습니다.')
            return redirect(url_for('login'))
        if not u['is_active']:
            flash('정지된 계정입니다.')
            return redirect(url_for('login'))
        # ── 세션 고정 공격 방지: 로그인 시 세션 재생성 ──
        session.clear()
        session.permanent = True   # 세션 만료 시간 적용
        session['user_id'] = u['id']
        return redirect(url_for('index'))
    return render_template('login.html', user=current_user())


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/mypage', methods=['GET', 'POST'])
@login_required
def mypage():
    db = get_db()
    u  = current_user()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_bio':
            db.execute('UPDATE users SET bio=? WHERE id=?',
                       (request.form.get('bio', '').strip(), u['id']))
            db.commit()
            flash('소개글이 업데이트되었습니다.')
        elif action == 'change_password':
            cur = request.form.get('current_password', '')
            new = request.form.get('new_password', '')
            if not check_password_hash(u['password'], cur):
                flash('현재 비밀번호가 틀렸습니다.')
            elif len(new) < 4:
                flash('새 비밀번호는 4자 이상이어야 합니다.')
            else:
                db.execute('UPDATE users SET password=? WHERE id=?',
                           (generate_password_hash(new), u['id']))
                db.commit()
                flash('비밀번호가 변경되었습니다.')
        elif action == 'delete_account':
            cur = request.form.get('confirm_password', '')
            if not check_password_hash(u['password'], cur):
                flash('비밀번호가 틀렸습니다.')
                return redirect(url_for('mypage'))
            if u['is_admin']:
                flash('관리자 계정은 삭제할 수 없습니다.')
                return redirect(url_for('mypage'))
            db.execute('DELETE FROM users WHERE id=?', (u['id'],))
            db.execute('DELETE FROM products WHERE seller_id=?', (u['id'],))
            db.commit()
            session.clear()
            flash('계정이 삭제되었습니다.')
            return redirect(url_for('index'))
        return redirect(url_for('mypage'))

    u = current_user()
    my_products = db.execute(
        'SELECT * FROM products WHERE seller_id=? ORDER BY created_at DESC', (u['id'],)
    ).fetchall()
    transactions = db.execute(
        """SELECT t.*, s.username AS sender_name, r.username AS receiver_name
           FROM transactions t
           JOIN users s ON t.sender_id=s.id
           JOIN users r ON t.receiver_id=r.id
           WHERE t.sender_id=? OR t.receiver_id=?
           ORDER BY t.created_at DESC LIMIT 20""",
        (u['id'], u['id'])
    ).fetchall()
    wishlists = db.execute(
        """SELECT p.* FROM wishlists w
           JOIN products p ON w.product_id=p.id
           WHERE w.user_id=? AND p.is_blocked=0
           ORDER BY w.created_at DESC""",
        (u['id'],)
    ).fetchall()
    return render_template('mypage.html', user=u,
                           my_products=my_products, transactions=transactions,
                           wishlists=wishlists)


@app.route('/users/<int:uid>')
def profile(uid):
    db = get_db()
    profile_user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not profile_user:
        flash('존재하지 않는 사용자입니다.')
        return redirect(url_for('index'))
    products = db.execute(
        'SELECT * FROM products WHERE seller_id=? AND is_blocked=0 AND is_sold=0 ORDER BY created_at DESC',
        (uid,)
    ).fetchall()
    return render_template('profile.html', profile_user=profile_user,
                           products=products, user=current_user())


@app.route('/chat')
@login_required
def chat():
    db = get_db()
    users = db.execute(
        'SELECT id, username FROM users WHERE is_active=1 AND id!=? ORDER BY username',
        (session['user_id'],)
    ).fetchall()
    history = db.execute(
        """SELECT m.*, u.username AS sender_name
           FROM messages m JOIN users u ON m.sender_id=u.id
           WHERE m.room='global'
           ORDER BY m.created_at DESC LIMIT 50"""
    ).fetchall()
    return render_template('chat.html', users=users,
                           history=list(reversed(history)), user=current_user())


@app.route('/dm/<int:uid>')
@login_required
def dm(uid):
    db = get_db()
    other = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not other:
        flash('존재하지 않는 사용자입니다.')
        return redirect(url_for('chat'))
    room = dm_room(session['user_id'], uid)
    history = db.execute(
        """SELECT m.*, u.username AS sender_name
           FROM messages m JOIN users u ON m.sender_id=u.id
           WHERE m.room=?
           ORDER BY m.created_at DESC LIMIT 50""",
        (room,)
    ).fetchall()
    return render_template('dm.html', other=other, room=room,
                           history=list(reversed(history)), user=current_user())


@app.route('/report', methods=['GET', 'POST'])
@login_required
def report():
    db = get_db()
    target_type = request.args.get('type') or request.form.get('type')
    target_id   = request.args.get('id')   or request.form.get('id')
    if not target_type or not target_id:
        flash('신고 대상이 없습니다.')
        return redirect(url_for('index'))
    target_id = int(target_id)

    if request.method == 'POST':
        reason = request.form.get('reason', '').strip()
        if not reason:
            flash('신고 사유를 입력하세요.')
            return redirect(url_for('report', type=target_type, id=target_id))
        already = db.execute(
            'SELECT id FROM reports WHERE reporter_id=? AND target_type=? AND target_id=?',
            (session['user_id'], target_type, target_id)
        ).fetchone()
        if already:
            flash('이미 신고한 대상입니다.')
            return redirect(url_for('index'))
        db.execute(
            'INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (?,?,?,?)',
            (session['user_id'], target_type, target_id, reason)
        )
        db.commit()
        blocked = apply_report_action(db, target_type, target_id)
        if blocked:
            if target_type == 'product':
                flash('신고 누적으로 상품이 차단되었습니다.')
            else:
                flash('신고 누적으로 사용자가 정지되었습니다.')
        else:
            flash('신고가 접수되었습니다.')
        return redirect(url_for('index'))

    target = None
    if target_type == 'product':
        target = db.execute(
            """SELECT p.*, u.username AS seller_name
               FROM products p JOIN users u ON p.seller_id=u.id WHERE p.id=?""",
            (target_id,)
        ).fetchone()
    elif target_type == 'user':
        target = db.execute('SELECT * FROM users WHERE id=?', (target_id,)).fetchone()

    return render_template('report.html', target=target, target_type=target_type,
                           target_id=target_id, user=current_user())


@app.route('/transfer', methods=['GET', 'POST'])
@login_required
def transfer():
    db = get_db()
    u  = current_user()
    if request.method == 'POST':
        receiver_username = request.form.get('receiver_username', '').strip()
        amount_str = request.form.get('amount', '0')
        if not amount_str.isdigit() or int(amount_str) <= 0:
            flash('올바른 금액을 입력하세요.')
            return redirect(url_for('transfer'))
        amount = int(amount_str)
        receiver = db.execute(
            'SELECT * FROM users WHERE username=? AND is_active=1', (receiver_username,)
        ).fetchone()
        if not receiver:
            flash('존재하지 않는 사용자입니다.')
            return redirect(url_for('transfer'))
        if receiver['id'] == u['id']:
            flash('자신에게는 송금할 수 없습니다.')
            return redirect(url_for('transfer'))
        if u['balance'] < amount:
            flash('잔액이 부족합니다.')
            return redirect(url_for('transfer'))
        db.execute('UPDATE users SET balance=balance-? WHERE id=?', (amount, u['id']))
        db.execute('UPDATE users SET balance=balance+? WHERE id=?', (amount, receiver['id']))
        db.execute(
            'INSERT INTO transactions (sender_id, receiver_id, amount) VALUES (?,?,?)',
            (u['id'], receiver['id'], amount)
        )
        db.commit()
        flash(f'{receiver_username}님에게 {amount:,}원 송금 완료!')
        return redirect(url_for('mypage'))

    users = db.execute(
        'SELECT id, username FROM users WHERE id!=? AND is_active=1 ORDER BY username',
        (u['id'],)
    ).fetchall()
    return render_template('transfer.html', user=u, users=users)


@app.route('/admin')
@admin_required
def admin():
    db = get_db()
    users = db.execute('SELECT * FROM users ORDER BY id').fetchall()
    products = db.execute(
        """SELECT p.*, u.username AS seller_name
           FROM products p JOIN users u ON p.seller_id=u.id ORDER BY p.id"""
    ).fetchall()
    reports = db.execute(
        """SELECT r.*, u.username AS reporter_name
           FROM reports r JOIN users u ON r.reporter_id=u.id ORDER BY r.id DESC"""
    ).fetchall()
    return render_template('admin.html', users=users, products=products,
                           reports=reports, user=current_user())


@app.route('/admin/users/<int:uid>/toggle', methods=['POST'])
@admin_required
def admin_toggle_user(uid):
    db = get_db()
    u = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if u and not u['is_admin']:
        db.execute('UPDATE users SET is_active=? WHERE id=?', (0 if u['is_active'] else 1, uid))
        db.commit()
    return redirect(url_for('admin'))


@app.route('/admin/products/<int:pid>/toggle', methods=['POST'])
@admin_required
def admin_toggle_product(pid):
    db = get_db()
    p = db.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()
    if p:
        db.execute('UPDATE products SET is_blocked=? WHERE id=?', (0 if p['is_blocked'] else 1, pid))
        db.commit()
    return redirect(url_for('admin'))


@app.route('/admin/reports/<int:rid>/delete', methods=['POST'])
@admin_required
def admin_delete_report(rid):
    db = get_db()
    db.execute('DELETE FROM reports WHERE id=?', (rid,))
    db.commit()
    return redirect(url_for('admin'))


# 소켓IO

@socketio.on('join')
def on_join(data):
    uid = session.get('user_id')
    if not uid:
        return
    room = data.get('room', '')
    if room == 'global':
        join_room(room)
    elif room.startswith('dm_'):
        # ── DM 방 접근 검증: 본인이 참여자인지 확인 ──
        parts = room.split('_')
        if len(parts) == 3:
            try:
                id1, id2 = int(parts[1]), int(parts[2])
                if uid in (id1, id2):
                    join_room(room)
            except ValueError:
                pass

@socketio.on('global_message')
def handle_global(data):
    uid = session.get('user_id')
    if not uid:
        return
    # ── 채팅 Rate Limiting ──
    if is_chat_rate_limited(uid):
        return
    db = get_db()
    u  = db.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone()
    content = data.get('content', '').strip()
    if not content:
        return
    db.execute('INSERT INTO messages (sender_id, room, content) VALUES (?,?,?)',
               (uid, 'global', content))
    db.commit()
    emit('global_message', {'sender': u['username'], 'content': content}, to='global')

@socketio.on('dm_message')
def handle_dm(data):
    uid = session.get('user_id')
    if not uid:
        return
    # ── 채팅 Rate Limiting ──
    if is_chat_rate_limited(uid):
        return
    db   = get_db()
    u    = db.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone()
    room = data.get('room', '')
    content = data.get('content', '').strip()
    if not content or not room.startswith('dm_'):
        return
    # ── DM 방 권한 재검증 ──
    parts = room.split('_')
    if len(parts) != 3:
        return
    try:
        id1, id2 = int(parts[1]), int(parts[2])
        if uid not in (id1, id2):
            return
    except ValueError:
        return
    db.execute('INSERT INTO messages (sender_id, room, content) VALUES (?,?,?)',
               (uid, room, content))
    db.commit()
    emit('dm_message', {'sender': u['username'], 'content': content}, to=room)


if __name__ == '__main__':
    init_db()
    socketio.run(app, debug=False)
