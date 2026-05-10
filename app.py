import os
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'

# --- 1. データベース設定 ---
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

# --- 2. データベースモデル（ここを大改造しました） ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    login_id = db.Column(db.String(50), unique=True, nullable=False) # ログイン用ID（変更不可）
    display_name = db.Column(db.String(50), nullable=False)        # 表示名（ニックネーム）
    password = db.Column(db.String(200), nullable=False)
    profile_text = db.Column(db.String(200), default="よろしくお願いします！") # マイページ用の一言

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(50), nullable=False)
    login_id = db.Column(db.String(50), nullable=False) # 誰が書いたか（IDで保存）
    content = db.Column(db.String(500), nullable=False)

class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True) # 鍵付きルーム用

with app.app_context():
     db.drop_all() # 構造を変えた直後は一度だけこれが必要
    db.create_all()

socketio = SocketIO(app, cors_allowed_origins="*")

# --- 3. ルート設定 ---

@app.route('/')
def home():
    return redirect(url_for('chat_list'))

# 【新規登録】IDと表示名の両方を保存
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

# 【ログイン】IDとパスワードで照合
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        l_id = request.form.get('login_id')
        pw = request.form.get('password')
        user = User.query.filter_by(login_id=l_id).first()
        if user and check_password_hash(user.password, pw):
            session['login_id'] = user.login_id # セッションにIDを記録
            return redirect(url_for('chat_list'))
        return "IDまたはパスワードが違います"
    return render_template('login.html')

# 【トーク一覧】
@app.route('/list')
def chat_list():
    if 'login_id' not in session:
        return redirect(url_for('login'))
    rooms = Room.query.all()
    return render_template('list.html', rooms=rooms)

# 【マイページ】
@app.route('/user/<target_id>')
def profile(target_id):
    if 'login_id' not in session:
        return redirect(url_for('login'))
    user = User.query.filter_by(login_id=target_id).first_or_404()
    is_mine = (session['login_id'] == user.login_id)
    return render_template('profile.html', user=user, is_mine=is_mine)

# 【チャットルーム】
@app.route('/chat/<room_name>', methods=['GET', 'POST'])
def chat_room(room_name):
    if 'login_id' not in session:
        return redirect(url_for('login'))
    room = Room.query.filter_by(name=room_name).first_or_404()
    # 鍵付き処理
    if room.password:
        if request.method == 'POST':
            if not check_password_hash(room.password, request.form.get('room_password')):
                return "パスワードが違います"
        else:
            return render_template('room_login.html', room_name=room_name)

    history = Message.query.filter_by(room=room_name).all()
    # 表示名を渡すために自分の情報を検索
    me = User.query.filter_by(login_id=session['login_id']).first()
    return render_template('index.html', room_name=room_name, display_name=me.display_name, history=history)

# 【部屋作成】
@app.route('/create_room', methods=['POST'])
def create_room():
    name = request.form.get('room_name')
    pw = request.form.get('room_password')
    if name and not Room.query.filter_by(name=name).first():
        hashed_pw = generate_password_hash(pw) if pw else None
        db.session.add(Room(name=name, password=hashed_pw))
        db.session.commit()
    return redirect(url_for('chat_list'))

@app.route('/logout')
def logout():
    session.pop('login_id', None)
    return redirect(url_for('login'))

# --- 4. SocketIO ---

@socketio.on('join')
def on_join(data):
    join_room(data['room'])

@socketio.on('message_from_client')
def handle_message(data):
    room = data['room']
    l_id = session.get('login_id')
    user = User.query.filter_by(login_id=l_id).first()
    
    # DB保存（login_idで紐付け）
    new_msg = Message(room=room, login_id=l_id, content=data['msg'])
    db.session.add(new_msg)
    db.session.commit()

    # 送信時は「表示名」を乗せて送る
    emit('message_from_server', {'username': user.display_name, 'msg': data['msg']}, room=room)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)