import os
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!' # 本来はランダムな文字列が望ましいです

# --- データベース設定（二刀流） ---
database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# --- モデル定義 ---
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(50), nullable=False)
    username = db.Column(db.String(50), nullable=False)
    content = db.Column(db.String(500), nullable=False)

with app.app_context():
    db.create_all()

# --- ルート設定 ---
@app.route('/')
def index():
    # テスト用に決め打ちでログイン状態を作ります
    # 本来はログイン画面から取得する値です
    session['username'] = "User1" 
    return render_template('index.html', room_name="Main Room", username=session['username'], history=[])

@app.route('/list')
def chat_list():
    return "Chat List Page (Coming Soon)"

# --- SocketIO イベント ---
@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)

@socketio.on('message_from_client')
def handle_message(data):
    room = data['room']
    username = session.get('username', 'Anonymous')
    msg_content = data['msg']

    # DBに保存
    new_msg = Message(room=room, username=username, content=msg_content)
    db.session.add(new_msg)
    db.session.commit()

    # 部屋にいる全員に送信
    emit('message_from_server', {'username': username, 'msg': msg_content}, room=room)

if __name__ == '__main__':
    socketio.run(app, debug=True)