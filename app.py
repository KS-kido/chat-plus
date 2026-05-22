import os
import uuid
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!' # セッション情報などの暗号化に使う秘密鍵

# --- 1. データベース・パス設定 ---
# サーバー内の絶対パスを取得
basedir = os.path.abspath(os.path.dirname(__file__))

# Render本番環境（PostgreSQL）か、ローカル（SQLite）かを自動で切り替える設定
database_url = os.environ.get('DATABASE_URL')
if database_url:
    # Renderの環境変数「postgres://」を最新の「postgresql://」に補正する処理
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "connect_args": {"sslmode": "require"} # 本番DBとの通信を安全に暗号化（SSL）
    }
else:
    # 開発環境用（PCローカルに「chat.db」というファイルを作る）
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'chat.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# プロフィール用のアイコン画像を保存するフォルダの場所（static/profile_pics）
UPLOAD_FOLDER = os.path.join('static', 'profile_pics')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- 2. データベースの初期化 ---
db = SQLAlchemy(app)

# 👥 ユーザー情報を管理するテーブル
class User(db.Model):
    __tablename__ = 'user'
    __table_args__ = {'extend_existing': True} # 既存テーブルがあっても上書き定義を許可
    id = db.Column(db.Integer, primary_key=True)
    login_id = db.Column(db.String(50), unique=True, nullable=False) # ログイン用の重複しないID
    display_name = db.Column(db.String(50), nullable=False)          # チャットに表示される名前
    password = db.Column(db.String(200), nullable=False)             # ハッシュ化されたパスワード
    profile_text = db.Column(db.String(200), default="よろしくお願いします！")
    profile_image = db.Column(db.String(100), default="default.png")  # 初期アイコン

# 💬 メッセージ内容を記録するテーブル
class Message(db.Model):
    __tablename__ = 'message'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(50), nullable=False)       # 投稿された部屋名
    login_id = db.Column(db.String(50), db.ForeignKey('user.login_id'), nullable=False) # 誰が書いたか
    content = db.Column(db.String(500), nullable=False)   # メッセージ本文
    # 投稿日時（日本時間：JSTに固定）。過去データがあってもエラーが出ないよう nullable=True に設定
    created_at = db.Column(db.DateTime, nullable=True, default=lambda: datetime.now(timezone(timedelta(hours=9))))
    
    # ユーザーテーブルとの紐付け（リレーション設定）
    user = db.relationship('User', backref='messages')

# 🚪 チャットルーム（部屋）を管理するテーブル
class Room(db.Model):
    __tablename__ = 'room'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False) # 部屋名
    password = db.Column(db.String(200), nullable=True)           # 任意で設定できる部屋のパスワード

# データベース移行（マイグレーション）用ツールをセット
migrate = Migrate(app, db)

# =======================================================
# 🔥 【Render無料プラン専用：アップデート衝突を安全にスルーする処理】
# =======================================================
with app.app_context():
    try:
        db.create_all() # まず足りないテーブルがあれば自動作成
        from sqlalchemy import text
        print("Executing column check on production database...")
        # 過去のDB構造に「created_at」がない場合を想定し、強制的にカラムを追加するSQLを発行
        db.session.execute(text('ALTER TABLE message ADD COLUMN created_at TIMESTAMP WITHOUT TIME ZONE NULL;'))
        db.session.commit()
        print("🚀 [SUCCESS] created_at column ensured!")
    except Exception as e:
        # すでにカラムが存在する場合はPostgreSQLがエラーを吐くので、ここで安全にロールバックして無視させる
        db.session.rollback()
        print(f"DB Row-Fix Notice (Column already exists - safely skipped): {e}")
# =======================================================

# 🔌 リアルタイム通信用の SocketIO を初期化
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# --- 3. ルート設定（画面の切り替え） ---

# 🏠 トップページにアクセスしたら部屋一覧（/list）へ強制ジャンプ
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
        # すでに同じIDが登録されていないかチェック
        if User.query.filter_by(login_id=l_id).first():
            return "このIDは使われています"
        # パスワードを暗号化（ハッシュ化）してデータベースに保存
        new_user = User(login_id=l_id, display_name=d_name, password=generate_password_hash(pw))
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')

# 🔓 ログイン画面
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        l_id = request.form.get('login_id')
        pw = request.form.get('password')
        user = User.query.filter_by(login_id=l_id).first()
        # ユーザーが存在し、かつ入力されたパスワードが正しければセッション（合鍵）を発行
        if user and check_password_hash(user.password, pw):
            session['login_id'] = user.login_id 
            return redirect(url_for('chat_list'))
        flash('IDまたはパスワードが違います')
        return redirect(url_for('login')) 
    return render_template('login.html')

# 🚪 チャットルーム一覧画面
@app.route('/list')
def chat_list():
    if 'login_id' not in session: # ログインしていない人はお断り
        return redirect(url_for('login'))
    rooms = Room.query.all() # 作成されているすべての部屋を取得
    return render_template('list.html', rooms=rooms)

# 👤 プロフィール確認画面
@app.route('/user/<target_id>')
def profile(target_id):
    if 'login_id' not in session:
        return redirect(url_for('login'))
    user = User.query.filter_by(login_id=target_id).first_or_404()
    is_mine = (session['login_id'] == user.login_id) # 見ているのが「自分自身」のページか判定
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
        # アイコン画像が新しくアップロードされた場合の処理
        if file and file.filename != '':
            ext = file.filename.rsplit('.', 1)[1].lower()
            # ファイル名の重複を防ぐため、ユーザーIDとランダムな文字列（UUID）を組み合わせて保存
            filename = f"{user.login_id}_{uuid.uuid4().hex}.{ext}"
            if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            user.profile_image = filename
        db.session.commit()
        flash('プロフィールを更新しました！')
        return redirect(url_for('profile', target_id=user.login_id))
    return render_template('edit_profile.html', user=user)

# 💬 個別のチャットルーム画面
@app.route('/chat/<room_name>', methods=['GET', 'POST'])
def chat_room(room_name):
    if 'login_id' not in session:
        return redirect(url_for('login'))
    room = Room.query.filter_by(name=room_name).first_or_404()
    # 部屋にパスワードがかかっている場合の認証処理
    if room.password:
        if request.method == 'POST':
            if not check_password_hash(room.password, request.form.get('room_password')):
                return "パスワードが違います"
        else:
            # まだパスワードを入力していないなら、パスワード入力専用の画面を表示
            return render_template('room_login.html', room_name=room_name)
            
    # 部屋の過去のトーク履歴をすべて取得
    history = Message.query.filter_by(room=room_name).all()
    me = User.query.filter_by(login_id=session['login_id']).first()
    return render_template('index.html', room_name=room_name, display_name=me.display_name, history=history)

# ➕ 新しいチャットルームを作成する処理
@app.route('/create_room', methods=['POST'])
def create_room():
    name = request.form.get('room_name')
    pw = request.form.get('room_password')
    # 部屋名が空でなく、かつ同じ名前の部屋がなければ新規作成
    if name and not Room.query.filter_by(name=name).first():
        hashed_pw = generate_password_hash(pw) if pw else None
        db.session.add(Room(name=name, password=hashed_pw))
        db.session.commit()
    return redirect(url_for('chat_list'))

# 🚪 ログアウト処理
@app.route('/logout')
def logout():
    session.pop('login_id', None) # セッション（合鍵）を破棄
    return redirect(url_for('login'))


# =======================================================
# ⚙️ 【超重要】PWA（アプリ化）設定ファイルを正しく返すルーティング
# =======================================================

# 🌐 1. manifest.json の配信ルート
@app.route('/manifest.json')
def serve_manifest():
    response = send_from_directory('static', 'manifest.json')
    # ブラウザに対して「これは設定用のJSONデータですよ」と正しくMIMEタイプを伝える
    response.headers['Content-Type'] = 'application/json'
    return response

# 🛠️ 2. sw.js（Service Worker）の配信ルート
# ※ AndroidのChromeは、ここが「application/javascript」かつ「ルート直下（/sw.js）」で配信されないとアプリとして絶対に認めない仕様になっています！
@app.route('/sw.js')
def serve_sw():
    response = send_from_directory('static', 'sw.js')
    # ブラウザに対して「これはただのテキストじゃなくて、アプリを動かすためのJavaScriptのプログラムですよ」と強制認識させる
    response.headers['Content-Type'] = 'application/javascript'
    return response


# --- 4. リアルタイム通信処理 (SocketIOイベント) ---

# 📥 ルームに入室した時（部屋の回線を繋ぐ）
@socketio.on('join')
def on_join(data):
    join_room(data['room'])

# 📥 クライアントからメッセージ（テキストまたは位置情報URL）が送信されてきた時
@socketio.on('message_from_client')
def handle_message(data):
    room = data['room']
    l_id = session.get('login_id')
    user = User.query.filter_by(login_id=l_id).first()
    
    # 投稿された瞬間の日本時間を取得
    jst_now = datetime.now(timezone(timedelta(hours=9)))
    
    # データベースに新規メッセージを保存
    new_msg = Message(room=room, login_id=l_id, content=data['msg'], created_at=jst_now)
    db.session.add(new_msg)
    db.session.commit()

    # 部屋にいる全員の画面に、新しいメッセージの吹き出しをリアルタイム配信（Server -> Clients）
    emit('message_from_server', {
        'id': new_msg.id,                # 後から消せるようにデータベース上のIDを渡す
        'username': user.display_name, 
        'msg': data['msg'],
        'login_id': l_id,
        'time': jst_now.strftime('%H:%M') # 「時:分」の形で綺麗にして送る
    }, room=room)

# 🗑️ クライアントからメッセージの「削除要求」が届いた時
@socketio.on('delete_message_from_client')
def handle_delete_message(data):
    message_id = data.get('message_id')
    room = data.get('room')
    login_id = session.get('login_id')

    # データベースから消したいメッセージを特定
    msg = Message.query.get(message_id)

    # メッセージが存在し、かつ「投稿した本人」の要求である場合のみ削除を実行
    if msg and msg.login_id == login_id:
        db.session.delete(msg)
        db.session.commit()
        # 部屋にいる全員に「このIDの吹き出しを画面から消して！」とリアルタイムに通知を飛ばす
        emit('message_deleted_from_server', {'message_id': message_id}, room=room)

# --- 5. 本番アプリの起動処理 ---
if __name__ == '__main__':
    # Render本番等の環境変数からポート番号を取得（なければデフォルトは5000番）
    port = int(os.environ.get("PORT", 5000))
    # Flask-SocketIOサーバーを起動
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)