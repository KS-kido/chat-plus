import os
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!' # セッション（ログイン情報）の暗号化に必要

# --- 1. データベース設定 ---
# Render環境(PostgreSQL)とローカル環境(SQLite)を自動で切り替えます
database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'chat.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- 2. データベースモデル（テーブル定義） ---
# ユーザー情報を保存するテーブル
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

# メッセージを保存するテーブル
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(50), nullable=False) # どの部屋の発言か
    username = db.Column(db.String(50), nullable=False) # 誰の発言か
    content = db.Column(db.String(500), nullable=False) # 内容

# チャットルームを管理するテーブル
class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    # パスワード列を追加（空でもOKにするため nullable=True）
    password = db.Column(db.String(200), nullable=True)


# 起動時にテーブルを作成（すでにある場合は何もしない）
with app.app_context():
    db.drop_all() # 構造をリセットしたい時だけコメントを外す
    db.create_all()

# --- 3. SocketIO初期化 ---
socketio = SocketIO(app, cors_allowed_origins="*")

# --- 4. 画面遷移（ルート設定） ---

# A. トップページ（アクセスしたらまずトーク一覧へ飛ばす）
@app.route('/')
def home():
    return redirect(url_for('chat_list'))

# B. ユーザー登録
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            return "そのユーザー名は使われています"
        
        # パスワードをハッシュ化して保存（セキュリティの基本！）
        new_user = User(username=username, password=generate_password_hash(password))
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')

# C. ログイン
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        # パスワードが一致したらセッションに名前を記録
        if user and check_password_hash(user.password, password):
            session['username'] = user.username
            return redirect(url_for('chat_list')) # ログイン後はトーク一覧へ
        return "ログイン失敗"
    return render_template('login.html')

# D. トーク一覧
@app.route('/list')
def chat_list():
    if 'username' not in session:
        return redirect(url_for('login'))
    rooms = Room.query.all()
    return render_template('list.html', rooms=rooms)

# E. 部屋の作成
@app.route('/create_room', methods=['POST'])
def create_room():
    room_name = request.form.get('room_name')
    room_password = request.form.get('room_password') # 追加
    
    if room_name and not Room.query.filter_by(name=room_name).first():
        # パスワードがあればハッシュ化して保存、なければ None
        hashed_pw = generate_password_hash(room_password) if room_password else None
        new_room = Room(name=room_name, password=hashed_pw)
        db.session.add(new_room)
        db.session.commit()
    return redirect(url_for('chat_list'))

# F. チャットルーム本体
@app.route('/chat/<room_name>', methods=['GET', 'POST'])
def chat_room(room_name):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    room = Room.query.filter_by(name=room_name).first_or_404()

    # パスワードが設定されている部屋の場合
    if room.password:
        # POST（パスワード入力後）でない場合は入力画面を出す
        if request.method == 'POST':
            input_pw = request.form.get('room_password')
            if check_password_hash(room.password, input_pw):
                # 合っていたらチャット画面へ（本来はここで入室許可をセッションに持つのが理想）
                pass 
            else:
                return "パスワードが違います。<a href='/list'>戻る</a>"
        else:
            # パスワード入力用の専用HTML（または簡易画面）を返す
            return render_template('room_login.html', room_name=room_name)

    history = Message.query.filter_by(room=room_name).all()
    return render_template('index.html', room_name=room_name, username=session['username'], history=history)

# G. ログアウト
@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

# --- 5. リアルタイム通信 (SocketIO) ---

@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room) # 指定された部屋の通信グループに入る

@socketio.on('message_from_client')
def handle_message(data):
    room = data['room']
    username = session.get('username', 'Anonymous')
    msg_content = data['msg']

    # メッセージをDBに永久保存
    new_msg = Message(room=room, username=username, content=msg_content)
    db.session.add(new_msg)
    db.session.commit()

    # 同じ部屋にいる人全員にメッセージを届ける
    emit('message_from_server', {'username': username, 'msg': msg_content}, room=room)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # 本番環境で動かすための allow_unsafe_werkzeug 設定
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)