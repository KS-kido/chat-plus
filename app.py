import os
import uuid
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'

# --- 1. データベース・パス設定 ---
basedir = os.path.abspath(os.path.dirname(__file__))

database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "connect_args": {"sslmode": "require"}
    }
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'chat.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# アイコン画像の保存先
UPLOAD_FOLDER = os.path.join('static', 'profile_pics')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- 2. データベースの初期化 ---
db = SQLAlchemy(app)

# --- モデル定義 ---
class User(db.Model):
    __tablename__ = 'user'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    login_id = db.Column(db.String(50), unique=True, nullable=False)
    display_name = db.Column(db.String(50), nullable=False)
    password = db.Column(db.String(200), nullable=False)
    profile_text = db.Column(db.String(200), default="よろしくお願いします！")
    profile_image = db.Column(db.String(100), default="default.png")

class Message(db.Model):
    __tablename__ = 'message'
    __table_args__ = {'extend_existing': True}
    
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(50), nullable=False)
    login_id = db.Column(db.String(50), db.ForeignKey('user.login_id'), nullable=False)
    content = db.Column(db.String(500), nullable=False)
    
    # 過去データがあっても共存できるよう nullable=True にしています
    created_at = db.Column(db.DateTime, nullable=True, default=lambda: datetime.now(timezone(timedelta(hours=9))))
    
    user = db.relationship('User', backref='messages')

class Room(db.Model):
    __tablename__ = 'room'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True)

# Migrateの初期化
migrate = Migrate(app, db)

# =======================================================
# 🔥 【Render無料プラン専用：本番DB無破壊アップデートパッチ】
# =======================================================
with app.app_context():
    try:
        # 1. まず通常のテーブルチェック
        db.create_all()
        
        # 2. 本番PostgreSQLに直接「created_at」列を強制追加する
        # ※ 過去のデータがNULLになっても怒られないよう、明示的に NULL（NULLを許容）を指定して直球で追加します
        from sqlalchemy import text
        print("Executing absolute column injection on production database...")
        db.session.execute(text('ALTER TABLE message ADD COLUMN created_at TIMESTAMP WITHOUT TIME ZONE NULL;'))
        db.session.commit()
        print("🚀 [SUCCESS] created_at column permanently added to PostgreSQL!")
        
    except Exception as e:
        # すでに列が存在する場合は、PostgreSQLが重複エラーを吐くので安全に無視してロールバックします
        db.session.rollback()
        print(f"DB Row-Fix Notice (This is normal if column already exists): {e}")
# =======================================================

# SocketIOの初期化
socketio = SocketIO(app, 
    cors_allowed_origins="*", 
    async_mode='threading'
)

# --- 3. ルート設定 ---

@app.route('/')
def home():
    return redirect(url_for('chat_list'))

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

@app.route('/login', methods=['GET', 'POST'])
def login():
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

@app.route('/list')
def chat_list():
    if 'login_id' not in session:
        return redirect(url_for('login'))
    rooms = Room.query.all()
    return render_template('list.html', rooms=rooms)

@app.route('/user/<target_id>')
def profile(target_id):
    if 'login_id' not in session:
        return redirect(url_for('login'))
    user = User.query.filter_by(login_id=target_id).first_or_404()
    is_mine = (session['login_id'] == user.login_id)
    return render_template('profile.html', user=user, is_mine=is_mine)

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

@app.route('/chat/<room_name>', methods=['GET', 'POST'])
def chat_room(room_name):
    if 'login_id' not in session:
        return redirect(url_for('login'))
    room = Room.query.filter_by(name=room_name).first_or_404()
    if room.password:
        if request.method == 'POST':
            if not check_password_hash(room.password, request.form.get('room_password')):
                return "パスワードが違います"
        else:
            return render_template('room_login.html', room_name=room_name)
    history = Message.query.filter_by(room=room_name).all()
    me = User.query.filter_by(login_id=session['login_id']).first()
    return render_template('index.html', room_name=room_name, display_name=me.display_name, history=history)

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

# --- 4. リアルタイム通信 (SocketIO) ---

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
        'username': user.display_name, 
        'msg': data['msg'],
        'login_id': l_id,
        'time': jst_now.strftime('%H:%M')
    }, room=room)

# --- 5. 実行処理 ---
if __name__ == '__main__':
    with app.app_context():
        try:
            # ローカル環境（SQLite）のためのシンプルな初期化だけに戻します。
            # 本番用の余計なALTER TABLE命令（パッチ）はすべて削除します。
            db.create_all() 
            print("Local Database setup completed.")
        except Exception as e:
            print(f"Local DB Setup Notice: {e}")

    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)