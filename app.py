import os
import uuid
from datetime import datetime, timezone, timedelta
# Flask（WEBサーバーのコア機能）や、画面遷移、セッション、ファイル配信に必要な各種機能をインポート
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, send_from_directory
from flask_sqlalchemy import SQLAlchemy     # データベースをPythonのコードで簡単に操作するためのライブラリ
from flask_socketio import SocketIO, emit, join_room  # リアルタイム通信（LINEのような即時送受信）を行うためのライブラリ
from werkzeug.security import generate_password_hash, check_password_hash  # パスワードを暗号化・照合する安全な機能
from flask_migrate import Migrate           # データベースの構造変更（マイグレーション）を管理するツール

# Flaskアプリの本体を初期化
app = Flask(__name__)
# セッション情報（ログイン状態の維持など）をサーバー側で安全に暗号化するための「合鍵（秘密鍵）」
app.config['SECRET_KEY'] = 'secret!' 

# =======================================================
# 📁 1. データベース・ファイル保存先の設定
# =======================================================
# このプログラムファイル（app.py）が置いてある場所の絶対パスを自動取得
basedir = os.path.abspath(os.path.dirname(__file__))

# Render（本番環境）にセットされているPostgreSQLの接続URLを取得
database_url = os.environ.get('DATABASE_URL')

if database_url:
    # 💡 補正処理：Renderの古い接続設定「postgres://」を、最新のSQLAlchemyが推奨する「postgresql://」に自動変換
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    # 本番データベースとの通信を安全に暗号化（SSL通信を強制）するための設定
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "connect_args": {"sslmode": "require"} 
    }
else:
    # 💻 ローカル（PostgreSQLに切り替え）
    app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:syana116@localhost:5432/chat_db'

# データベース変更時に余計なメモリを消費する警告通知機能をオフに設定
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# プロフィール用のアイコン画像を保存するフォルダの場所（static/profile_pics）を指定
UPLOAD_FOLDER = os.path.join('static', 'profile_pics')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# =======================================================
# 👥 2. データベースのテーブル（設計図）の定義
# =======================================================
db = SQLAlchemy(app)

# 【ユーザー情報を管理するテーブル】
class User(db.Model):
    __tablename__ = 'user'
    __table_args__ = {'extend_existing': True} # すでに本番DBにテーブルがあっても競合エラーにせず、上書き定義を許可する
    id = db.Column(db.Integer, primary_key=True)
    login_id = db.Column(db.String(50), unique=True, nullable=False) # ログインID（重複不可・必須）
    display_name = db.Column(db.String(50), nullable=False)          # チャットに表示される名前（必須）
    password = db.Column(db.String(200), nullable=False)             # ハッシュ化（暗号化）されたパスワード（必須）
    profile_text = db.Column(db.String(200), default="よろしくお願いします！") # 一言コメント
    profile_image = db.Column(db.String(100), default="default.png")  # アイコンの画像ファイル名

# 【チャットのメッセージ内容を記録するテーブル】
class Message(db.Model):
    __tablename__ = 'message'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(50), nullable=False)       # 投稿された部屋の名前（必須）
    # 外部キー設定：Userテーブルの「login_id」とこのメッセージを紐付ける（誰が書いたか）
    login_id = db.Column(db.String(50), db.ForeignKey('user.login_id'), nullable=False) 
    content = db.Column(db.String(500), nullable=False)   # チャットの本文（必須）
    # 投稿日時（初期値：日本時間（JST）の現在時刻を自動挿入）
    created_at = db.Column(db.DateTime, nullable=True, default=lambda: datetime.now(timezone(timedelta(hours=9))))
    
    # データベースの「リレーションシップ」機能。Message.user と書くだけで、投稿した人のユーザー情報（ display_name など）を一発で引っ張ってこれるようにする設定
    user = db.relationship('User', backref='messages')

# 【チャットルームを管理するテーブル】
class Room(db.Model):
    __tablename__ = 'room'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False) # 部屋の名前（重複不可・必須）
    password = db.Column(db.String(200), nullable=True)           # 部屋のパスワード（任意、なしでもOK）
    category = db.Column(db.String(50), nullable=False, default="未分類") # 💡【追加】カテゴリ項目

# コマンド等でDB構造を管理するためのマイグレーション機能をセット
migrate = Migrate(app, db)

# 💡 データベースの接続を処理ごとに毎回リフレッシュし、Neonの自動スリープを確実に動かす設定
@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()

# 🛠️ 【自動修復処理】
with app.app_context():
    try:
        # 💡 ここで text をインポートしておくことで、直下の処理すべてでエラーが出なくなります
        from sqlalchemy import text
        
        db.create_all() # 新しいテーブルがまだDBになければ自動作成する
        print("Executing column check on database...")
        
        # ⚠️ Messageテーブル用
        try:
            db.session.execute(text('ALTER TABLE message ADD COLUMN created_at TIMESTAMP WITHOUT TIME ZONE NULL;'))
        except Exception:
            db.session.rollback()
        
        # 💡 Roomテーブルに「category」カラムを自動で追加・確認する処理
        try:
            db.session.execute(text("ALTER TABLE room ADD COLUMN category VARCHAR(50) NOT NULL DEFAULT '未分類';"))
            print("🚀 [SUCCESS] category column added to room table!")
        except Exception as e:
            db.session.rollback() # すでにカラムがある、または新規作成時で不要な場合はスルー
            print(f"Room category column check skipped: {e}")
            
        db.session.commit() # 確定
        print("🚀 [SUCCESS] DB initialization and migration check completed!")
    except Exception as e:
        db.session.rollback()
        print(f"DB Row-Fix Notice: {e}")


# LINEのようなリアルタイムの双方向通信（Socket.IO）を有効化
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# =======================================================
# 🌐 3. ルート設定（URLにアクセスされたときの画面切り替え）
# =======================================================

# 🏠 トップページ
@app.route('/')
def home():
    return redirect(url_for('chat_list'))

# 📝 新規ユーザー登録画面
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        l_id = request.form.get('login_id')
        d_name = request.form.get('display_name')
        pw = request.form.get('password')
        
        if User.query.filter_by(login_id=l_id).first():
            return "このIDは使われています"
            
        new_user = User(login_id=l_id, display_name=d_name, password=generate_password_hash(pw))
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
        
    return render_template('signup.html')

# 🔓 ログイン画面
@app.route('/login', methods=['GET', 'POST'])
def login():
    user_agent = request.headers.get('User-Agent', '').lower()
    if 'line/' in user_agent:
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>ブラウザで開き直しています...</title>
            <script>
                let currentUrl = window.location.href;
                let sep = currentUrl.includes('?') ? '&' : '?';
                window.location.href = currentUrl + sep + 'openExternalBrowser=1';
            </script>
        </head>
        <body>
            <p style="text-align:center; margin-top:50px; font-family:sans-serif; color:#666;">
                LINEから通常のブラウザへ切り替えています。<br>しばらくお待ちください...
            </p>
        </body>
        </html>
        """

    if request.method == 'POST':
        l_id = request.form.get('login_id')
        pw = request.form.get('password')
        
        user = User.query.filter_by(login_id=l_id).first()
        
        if user and check_password_hash(user.password, pw):
            session['login_id'] = user.login_id 
            return redirect(url_for('chat_list'))
            
        flash('IDまたはパスワードが違います')
        return redirect(url_for('login')) 
        
    return render_template('login.html')

# 🚪 チャットルーム一覧画面
@app.route('/list')
def chat_list():
    if 'login_id' not in session: 
        return redirect(url_for('login'))
        
    rooms = Room.query.all() 
    return render_template('list.html', rooms=rooms)

# 👤 プロフィール確認画面
@app.route('/user/<target_id>')
def profile(target_id):
    if 'login_id' not in session:
        return redirect(url_for('login'))
        
    user = User.query.filter_by(login_id=target_id).first_or_404()
    is_mine = (session['login_id'] == user.login_id) 
    return render_template('profile.html', user=user, is_mine=is_mine)

# ✏️ プロフィール編集画面
@app.route('/edit_profile', methods=['GET', 'POST'])
def edit_profile():
    if 'login_id' not in session:
        return redirect(url_for('login'))
        
    user = User.query.filter_by(login_id=session['login_id']).first()
    
    if request.method == 'POST':
        user.display_name = request.form.get('display_name')
        user.profile_text = request.form.get('profile_text')
        
        file = request.files.get('profile_image')
        if file and file.filename != '':
            ext = file.filename.rsplit('.', 1)[1].lower()
            filename = f"{user.login_id}_{uuid.uuid4().hex}.{ext}"
            
            if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])
                
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            user.profile_image = filename
            
        db.session.commit()
        flash('プロフィールを更新しました！')
        return redirect(url_for('profile', target_id=user.login_id))
        
    return render_template('edit_profile.html', user=user)

# ➕ 新しいチャットルームを作成する処理
@app.route('/create_room', methods=['POST'])
def create_room():
    if 'login_id' not in session:
        return redirect(url_for('login'))

    name = request.form.get('room_name')
    pw = request.form.get('room_password')
    category = request.form.get('room_category', '未分類') # 画面からカテゴリを取得
    
    if name and not Room.query.filter_by(name=name).first():
        hashed_pw = generate_password_hash(pw) if pw else None # パスワードがあれば暗号化
        # category も一緒にしっかり保存する
        db.session.add(Room(name=name, password=hashed_pw, category=category))
        db.session.commit()
    return redirect(url_for('chat_list'))

# 💬 各チャットルーム画面（ここが削れてしまっていました！）
@app.route('/chat/<room_name>', methods=['GET', 'POST'])
def chat(room_name):
    if 'login_id' not in session:
        return redirect(url_for('login'))
        
    room_data = Room.query.filter_by(name=room_name).first_or_404()
    
    # 🔒 部屋にパスワードがかかっている場合
    if room_data.password:
        session_key = f"unlocked_{room_name}"
        if not session.get(session_key):
            if request.method == 'POST':
                input_pw = request.form.get('room_password')
                if check_password_hash(room_data.password, input_pw):
                    session[session_key] = True
                    return redirect(url_for('chat', room_name=room_name))
                else:
                    flash('パスワードが間違っています')
                    return redirect(url_for('chat', room_name=room_name))
            return render_template('room_login.html', room_name=room_name)
            
    # パスワードを突破、または最初から無しの場合はチャット画面（index.html）を表示
    history = Message.query.filter_by(room=room_name).all()
    me = User.query.filter_by(login_id=session['login_id']).first()
    return render_template('index.html', room_name=room_name, display_name=me.display_name, history=history)

# 🚪 ログアウト処理
@app.route('/logout')
def logout():
    session.pop('login_id', None)
    return redirect(url_for('login'))


# =======================================================
# ⚙️ 4. PWA（スマホアプリ化）の設定ファイル配信
# =======================================================

@app.route('/manifest.json')
def serve_manifest():
    response = send_from_directory('static', 'manifest.json')
    response.headers['Content-Type'] = 'application/json'
    return response

@app.route('/sw.js')
def serve_sw():
    response = send_from_directory('static', 'sw.js')
    response.headers['Content-Type'] = 'application/javascript'
    return response


# =======================================================
# 📡 5. リアルタイム通信処理（Socket.IO）
# =======================================================

@socketio.on('join')
def on_join(data):
    join_room(data['room'])

@socketio.on('message_from_client')
def handle_message(data):
    room = data['room']
    l_id = session.get('login_id')
    user = User.query.filter_by(login_id=l_id).first()
    jst_now = datetime.now(timezone(timedelta(hours=9)))
    
    new_msg = Message(room=room, login_id=l_id, content=data['msg'], created_at=jst_now)
    db.session.add(new_msg)
    db.session.commit()

    emit('message_from_server', {
        'id': new_msg.id,
        'username': user.display_name,
        'msg': data['msg'],
        'login_id': l_id,
        'time': jst_now.strftime('%H:%M')
    }, room=room)

@socketio.on('delete_message_from_client')
def handle_delete_message(data):
    message_id = data.get('message_id')
    room = data.get('room')
    login_id = session.get('login_id')

    msg = Message.query.get(message_id)
    if msg and msg.login_id == login_id:
        db.session.delete(msg)
        db.session.commit()
        emit('message_deleted_from_server', {'message_id': message_id}, room=room)

# =======================================================
# 🚀 6. WEBサーバーの起動
# =======================================================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)