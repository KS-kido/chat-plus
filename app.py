import os
import uuid # ファイル名にランダムな文字列を付与するために使用
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'

# --- 1. データベース・保存先設定 ---
database_url = os.environ.get('DATABASE_URL')
if database_url:
    # Render等のPostgreSQL環境用（postgres:// を postgresql:// に変換）
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # ローカル開発用のSQLite設定
    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'chat.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# アイコン画像の保存先フォルダを指定
UPLOAD_FOLDER = 'static/profile_pics'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- 2. データベースとMigrateの初期化 ---
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# --- 2. データベースモデル定義 ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    login_id = db.Column(db.String(50), unique=True, nullable=False) # ログイン用ID
    display_name = db.Column(db.String(50), nullable=False)        # 表示名
    password = db.Column(db.String(200), nullable=False)
    profile_text = db.Column(db.String(200), default="よろしくお願いします！") # 自己紹介
    profile_image = db.Column(db.String(100), default="default.png") # アイコン画像名

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(50), nullable=False)
    login_id = db.Column(db.String(50), nullable=False) # 発言者のID
    content = db.Column(db.String(500), nullable=False)

class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True) # 鍵付き用パスワード

# アプリ起動時にテーブルを自動作成（既存のものは維持される）
with app.app_context():
    db.create_all()

socketio = SocketIO(app, cors_allowed_origins="*")

# --- 3. ルート設定 ---

@app.route('/')
def home():
    return redirect(url_for('chat_list'))

# 【新規登録】
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

# 【ログイン】
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        l_id = request.form.get('login_id')
        pw = request.form.get('password')
        user = User.query.filter_by(login_id=l_id).first()

        # パスワード照合成功時
        if user and check_password_hash(user.password, pw):
            session['login_id'] = user.login_id 
            return redirect(url_for('chat_list'))
        
        # 失敗時：flashメッセージを表示してログイン画面をリロード
        flash('IDまたはパスワードが違います')
        return redirect(url_for('login')) 
        
    return render_template('login.html')

# 【トークルーム一覧】
@app.route('/list')
def chat_list():
    if 'login_id' not in session:
        return redirect(url_for('login'))
    rooms = Room.query.all()
    return render_template('list.html', rooms=rooms)

# 【ユーザープロフィール表示】
@app.route('/user/<target_id>')
def profile(target_id):
    if 'login_id' not in session:
        return redirect(url_for('login'))
    user = User.query.filter_by(login_id=target_id).first_or_404()
    # 閲覧しているページが自分のものかどうかを判定
    is_mine = (session['login_id'] == user.login_id)
    return render_template('profile.html', user=user, is_mine=is_mine)

# 【マイページ編集】
@app.route('/edit_profile', methods=['GET', 'POST'])
def edit_profile():
    if 'login_id' not in session:
        return redirect(url_for('login'))
        
    user = User.query.filter_by(login_id=session['login_id']).first()
    
    if request.method == 'POST':
        # テキスト情報の更新
        user.display_name = request.form.get('display_name')
        user.profile_text = request.form.get('profile_text')
        
        # 画像ファイルがアップロードされた場合の処理
        file = request.files.get('profile_image')
        if file and file.filename != '':
            # 拡張子（jpg, png等）を取得
            ext = file.filename.rsplit('.', 1)[1].lower()
            # 「ユーザーID_ランダム文字列.拡張子」という名前で保存（重複・上書き防止）
            filename = f"{user.login_id}_{uuid.uuid4().hex}.{ext}"
            
            # 保存先フォルダが存在しない場合は自動作成
            if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])
                
            # 指定のパスにファイルを保存し、DBにファイル名を記録
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            user.profile_image = filename
            
        db.session.commit()
        flash('プロフィールを更新しました！')
        return redirect(url_for('profile', target_id=user.login_id))
        
    return render_template('edit_profile.html', user=user)

# 【チャットルーム入室・履歴表示】
@app.route('/chat/<room_name>', methods=['GET', 'POST'])
def chat_room(room_name):
    if 'login_id' not in session:
        return redirect(url_for('login'))
    room = Room.query.filter_by(name=room_name).first_or_404()
    
    # 部屋にパスワードがかかっている場合の処理
    if room.password:
        if request.method == 'POST':
            if not check_password_hash(room.password, request.form.get('room_password')):
                return "パスワードが違います"
        else:
            return render_template('room_login.html', room_name=room_name)

    history = Message.query.filter_by(room=room_name).all()
    me = User.query.filter_by(login_id=session['login_id']).first()
    return render_template('index.html', room_name=room_name, display_name=me.display_name, history=history)

# 【部屋の新規作成】
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

# --- 4. Real-time Communication (SocketIO) ---

@socketio.on('join')
def on_join(data):
    # クライアントを指定したルームに参加させる
    join_room(data['room'])

@socketio.on('message_from_client')
def handle_message(data):
    room = data['room']
    l_id = session.get('login_id')
    user = User.query.filter_by(login_id=l_id).first()
    
    # メッセージをデータベースに保存
    new_msg = Message(room=room, login_id=l_id, content=data['msg'])
    db.session.add(new_msg)
    db.session.commit()

    # 部屋にいる全員に「名前」と「メッセージ」を送信
    emit('message_from_server', {'username': user.display_name, 'msg': data['msg']}, room=room)

if __name__ == '__main__':
    # 開発環境と本番環境（Render等）の両方に対応するポート設定
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)