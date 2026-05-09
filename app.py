import os
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SECRET_KEY'] = 'secret!'

# --- DB設定 ---
database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'chat.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- SocketIO初期化（async_modeを指定せず自動選択に任せる） ---
socketio = SocketIO(app, cors_allowed_origins="*")

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(50), nullable=False)
    username = db.Column(db.String(50), nullable=False)
    content = db.Column(db.String(500), nullable=False)

# --- DBリセット・作成ブロック ---
with app.app_context():
    try:
        # 【重要】古い不完全なテーブルを一度確実に消去
        db.drop_all()
        db.create_all()
        print("Database re-initialized.")
    except Exception as e:
        print(f"Database error: {e}")

@app.route('/')
def index():
    session['username'] = "User1"
    history = Message.query.filter_by(room="Main Room").all()
    return render_template('index.html', room_name="Main Room", username=session['username'], history=history)

# ... (on_join, handle_message は以前のままでOK) ...

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # allow_unsafe_werkzeug=True を追加して、開発用サーバーでの起動を許可する
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)