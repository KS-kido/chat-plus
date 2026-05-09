import eventlet
eventlet.monkey_patch()
import os
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SECRET_KEY'] = 'secret!'

# --- DB設定（二刀流） ---
# --- DB設定 ---
database_url = os.environ.get('DATABASE_URL')
if database_url:
    # Render用（PostgreSQL）
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # ローカル用（SQLite）: 絶対パスで chat.db を指定
    # これで「app.py と同じ場所」に必ずDBが作られます
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'chat.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
# --- こ起動時にテーブルを自動作成する魔法のコード ---
with app.app_context():
    try:
        db.create_all()
        print("Database tables created or updated.")
    except Exception as e:
        print(f"Database error: {e}")

# --- SocketIO初期化（ここがポイント） ---
# async_modeを明示的に指定します
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet' if os.environ.get('DATABASE_URL') else None)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(50), nullable=False)
    username = db.Column(db.String(50), nullable=False)
    content = db.Column(db.String(500), nullable=False)

with app.app_context():
    db.create_all()

@app.route('/')
def index():
    session['username'] = "User1" # テスト用
    # 履歴をDBから取得して渡すように追加
    history = Message.query.filter_by(room="Main Room").all()
    return render_template('index.html', room_name="Main Room", username=session['username'], history=history)

@app.route('/list')
def chat_list():
    return "Chat List Page"

@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)

@socketio.on('message_from_client')
def handle_message(data):
    room = data['room']
    username = session.get('username', 'Anonymous')
    msg_content = data['msg']

    # 保存
    new_msg = Message(room=room, username=username, content=msg_content)
    db.session.add(new_msg)
    db.session.commit()

    # 送信
    emit('message_from_server', {'username': username, 'msg': msg_content}, room=room)

if __name__ == '__main__':
    # Renderが割り当てるポート番号を自動取得
    port = int(os.environ.get("PORT", 5000))
    # eventlet環境下でサーバーを直接起動
    socketio.run(app, host='0.0.0.0', port=port)