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
    # 💻 ローカル（自分のPC）でテストする場合は、自動的に同じフォルダ内に「chat.db」という軽量データベースファイルを作成
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'chat.db')

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

# コマンド等でDB構造を管理するためのマイグレーション機能をセット
migrate = Migrate(app, db)

# 🛠️ 【Render無料プラン専用：アップデート衝突を安全にスルーする自動修復処理】
with app.app_context():
    try:
        db.create_all() # 新しいテーブル（Roomなど）がまだDBになければ自動作成する
        from sqlalchemy import text
        print("Executing column check on production database...")
        # 過去のDB（Messageテーブル）に「created_at」カラムが存在しない場合を想定し、強制追加のSQL文を発行
        db.session.execute(text('ALTER TABLE message ADD COLUMN created_at TIMESTAMP WITHOUT TIME ZONE NULL;'))
        db.session.commit()
        print("🚀 [SUCCESS] created_at column ensured!")
    except Exception as e:
        # すでにカラムが存在する場合、PostgreSQLはエラーを返して処理を止めようとするため、
        # ここで変更を「ロールバック（取り消し）」して、何事もなかったかのように安全に無視させます（エラーログに出るやつをキャッチします）
        db.session.rollback()
        print(f"DB Row-Fix Notice (Column already exists - safely skipped): {e}")

# LINEのようなリアルタイムの双方向通信（Socket.IO）を有効化
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# =======================================================
# 🌐 3. ルート設定（URLにアクセスされたときの画面切り替え）
# =======================================================

# 🏠 トップページ（ https://〜/ ）にアクセスされた場合
@app.route('/')
def home():
    # 自動的にチャット部屋一覧（/list）の画面へ強制移動（リダイレクト）させる
    return redirect(url_for('chat_list'))

# 📝 新規ユーザー登録画面（ https://〜/signup ）
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    # ユーザーが「登録」ボタンを押してデータが送られてきた時（POST）
    if request.method == 'POST':
        l_id = request.form.get('login_id')
        d_name = request.form.get('display_name')
        pw = request.form.get('password')
        
        # 入力されたIDが、すでに他の人に使われていないかチェック
        if User.query.filter_by(login_id=l_id).first():
            return "このIDは使われています"
            
        # パスワードを生のまま保存するのは危険なため、強固に暗号化（ハッシュ化）してデータベースに新規保存
        new_user = User(login_id=l_id, display_name=d_name, password=generate_password_hash(pw))
        db.session.add(new_user)
        db.session.commit() # 確定
        return redirect(url_for('login')) # 登録が終わったらログイン画面へジャンプ
        
    # 普通にURLを開いた時（GET）は、登録画面のHTMLを表示
    return render_template('signup.html')

# 🔓 ログイン画面（ https://〜/login ）
@app.route('/login', methods=['GET', 'POST'])
def login():
    # 📱 【新機能】LINEアプリ内ブラウザ（インアプリブラウザ）の自動検知
    user_agent = request.headers.get('User-Agent', '').lower()
    if 'line/' in user_agent:
        # LINEの中で開かれている場合は、スマホの標準ブラウザ（SafariやChrome）を裏で叩き起こして
        # 同じURLを開き直させる特別な指示用HTMLを即座に返して強制脱出させます
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>ブラウザで開き直しています...</title>
            <script>
                let currentUrl = window.location.href;
                let sep = currentUrl.includes('?') ? '&' : '?';
                // URLの後ろに「?openExternalBrowser=1」を付けるとLINEが外部ブラウザを自動起動する仕様を利用
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

    # ユーザーが「ログインID」と「パスワード」を入力してボタンを押した時（POST）
    if request.method == 'POST':
        l_id = request.form.get('login_id')
        pw = request.form.get('password')
        
        # 入力されたIDのユーザーデータがデータベースにあるか探す
        user = User.query.filter_by(login_id=l_id).first()
        
        # ユーザーが存在し、かつ暗号化されたパスワードと入力されたパスワードが一致するか照合
        if user and check_password_hash(user.password, pw):
            # 一致したら、セッション（サーバーが発行する合鍵）にユーザーIDを記録してログイン状態にする
            session['login_id'] = user.login_id 
            return redirect(url_for('chat_list')) # 部屋一覧へ案内
            
        # 間違っていた場合は画面に警告メッセージを出す準備をして、ログイン画面をリロード
        flash('IDまたはパスワードが違います')
        return redirect(url_for('login')) 
        
    # 普通にアクセスされた時は、ログイン画面のHTMLを表示
    return render_template('login.html')

# 🚪 チャットルーム一覧画面（ https://〜/list ）
@app.route('/list')
def chat_list():
    # セッションに合鍵がない（ログインしていない）不審なアクセスは、ログイン画面へ強制送還
    if 'login_id' not in session: 
        return redirect(url_for('login'))
        
    # 現在データベースに登録されている全てのチャットルームを取得して画面に渡す
    rooms = Room.query.all() 
    return render_template('list.html', rooms=rooms)

# 👤 プロフィール確認画面（ https://〜/user/ユーザーID ）
@app.route('/user/<target_id>')
def profile(target_id):
    if 'login_id' not in session:
        return redirect(url_for('login'))
        
    # 指定されたユーザーIDをDBから取得（存在しなければ自動で「404 Not Found」エラー画面を出す）
    user = User.query.filter_by(login_id=target_id).first_or_404()
    # 今見ているプロフィールページが「自分自身」のものかどうかを判定（編集ボタンを出す出さないの制御用）
    is_mine = (session['login_id'] == user.login_id) 
    return render_template('profile.html', user=user, is_mine=is_mine)

# ✏️ プロフィール編集画面（ https://〜/edit_profile ）
@app.route('/edit_profile', methods=['GET', 'POST'])
def edit_profile():
    if 'login_id' not in session:
        return redirect(url_for('login'))
        
    user = User.query.filter_by(login_id=session['login_id']).first()
    
    # 変更を保存ボタンが押された時（POST）
    if request.method == 'POST':
        user.display_name = request.form.get('display_name')
        user.profile_text = request.form.get('profile_text')
        
        # アップロードされたアイコン画像ファイルを取得
        file = request.files.get('profile_image')
        if file and file.filename != '':
            ext = file.filename.rsplit('.', 1)[1].lower() # 拡張子（pngやjpg）を切り出し
            # ファイル名が他人と被って上書きされるのを防ぐため、「ユーザーID + ランダムな文字列」の唯一無二のファイル名に変換
            filename = f"{user.login_id}_{uuid.uuid4().hex}.{ext}"
            
            # 画像保存用フォルダ（static/profile_pics）がまだ無ければ自動作成
            if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])
                
            # サーバーに画像を物理保存し、DBにそのファイル名を記録
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            user.profile_image = filename
            
        db.session.commit() # 変更を確定
        flash('プロフィールを更新しました！')
        return redirect(url_for('profile', target_id=user.login_id))
        
    return render_template('edit_profile.html', user=user)

# 💬 各チャットルーム画面（ https://〜/chat/部屋名 ）
@app.route('/chat/<room_name>', methods=['GET', 'POST'])
def chat_room(room_name):
    if 'login_id' not in session:
        return redirect(url_for('login'))
        
    # 指定された部屋が実在するかDBを捜索
    room = Room.query.filter_by(name=room_name).first_or_404()
    
    # もし部屋にパスワードが設定されている場合
    if room.password:
        if request.method == 'POST':
            # 入力された鍵が違っていればストップ
            if not check_password_hash(room.password, request.form.get('room_password')):
                return "パスワードが違います"
        else:
            # まだパスワードを入力していない場合、専用のパスワード入力フォーム（room_login.html）を表示して遮断
            return render_template('room_login.html', room_name=room_name)
            
    # パスワードを突破、または最初から無しの場合は、その部屋の過去の全トーク履歴を古い順に取得
    history = Message.query.filter_by(room=room_name).all()
    # 自分の名前などの情報をDBから持ってきて、チャット画面（index.html）を表示
    me = User.query.filter_by(login_id=session['login_id']).first()
    return render_template('index.html', room_name=room_name, display_name=me.display_name, history=history)

# ➕ 新しいチャットルームを作成する裏処理（一覧画面のフォームからPOSTで呼ばれる）
@app.route('/create_room', methods=['POST'])
def create_room():
    name = request.form.get('room_name')
    pw = request.form.get('room_password')
    
    # 部屋名が空っぽでなく、かつすでに同じ名前の部屋が世界になければ新規作成
    if name and not Room.query.filter_by(name=name).first():
        hashed_pw = generate_password_hash(pw) if pw else None # パスワードがあれば暗号化
        db.session.add(Room(name=name, password=hashed_pw))
        db.session.commit()
    return redirect(url_for('chat_list')) # 作成後は部屋一覧へ戻る

# 🚪 ログアウト処理（ https://〜/logout ）
@app.route('/logout')
def logout():
    session.pop('login_id', None) # セッションから合鍵を消去（これで未ログイン状態になる）
    return redirect(url_for('login')) # ログイン画面へ戻す


# =======================================================
# ⚙️ 4. 【最重要】PWA（スマホアプリ化）のための設定ファイル配信
# =======================================================

# 🌐 1. manifest.json（アプリのアイコンや名前の設計図）を返すルート
@app.route('/manifest.json')
def serve_manifest():
    response = send_from_directory('static', 'manifest.json')
    # 💡 重要：ブラウザに対して「これはただのテキストじゃなく、アプリの設定JSONデータだよ」とMIMEタイプを厳格に伝える
    response.headers['Content-Type'] = 'application/json'
    return response

# 🛠️ 2. sw.js（アプリをスマホに登録する心臓部：サービスワーカー）を返すルート
# ⚠️ AndroidのChrome等は、ここが「URLのルート直下（/sw.js）」で配信されないとセキュリティルール上、アプリとして絶対に認めない仕様になっています！
@app.route('/sw.js')
def serve_sw():
    response = send_from_directory('static', 'sw.js')
    # 💡 重要：ブラウザに「これはバックグラウンドで動く、アプリ化のためのJavaScriptプログラムだよ」と強制認識させる
    response.headers['Content-Type'] = 'application/javascript'
    return response


# =======================================================
# 📡 5. リアルタイム通信処理（Socket.IOイベントの送受信）
# =======================================================

# 📥 クライアント（スマホ画面）が特定のチャットルームに接続してきた時
@socketio.on('join')
def on_join(data):
    # そのユーザーを「指定された部屋名」の専用通信回線（グループ）に所属させる
    join_room(data['room'])

# 📥 クライアントから「メッセージ（または位置情報URL）」がパッと送信されてきた時
@socketio.on('message_from_client')
def handle_message(data):
    room = data['room']
    l_id = session.get('login_id')
    user = User.query.filter_by(login_id=l_id).first()
    # 投稿された瞬間の正確な「日本時間（JST）」を計算
    jst_now = datetime.now(timezone(timedelta(hours=9)))
    
    # データベースに誰が、どこに、何を、いつ書いたかをしっかり記録
    new_msg = Message(room=room, login_id=l_id, content=data['msg'], created_at=jst_now)
    db.session.add(new_msg)
    db.session.commit()

    # 📡 同じチャットルームの回線に入っている「全員のスマホ画面」に向けて、
    # 新しいメッセージデータをリアルタイムに一斉ブロードキャスト（即時配信）
    emit('message_from_server', {
        'id': new_msg.id,                 # 後から本人がゴミ箱ボタンを押して消せるようにDBのメッセージIDを添付
        'username': user.display_name,   # 送信者の名前
        'msg': data['msg'],              # メッセージ本文（または位置情報のURL）
        'login_id': l_id,                # 自分のメッセージか相手のかを判別させるためのID
        'time': jst_now.strftime('%H:%M') # 「時:分」の形に綺麗に整えた文字列
    }, room=room)

# 🗑️ クライアントから「このメッセージを消して！」と削除要求（ゴミ箱タップ）が届いた時
@socketio.on('delete_message_from_client')
def handle_delete_message(data):
    message_id = data.get('message_id')
    room = data.get('room')
    login_id = session.get('login_id')

    # 該当のメッセージをDBから検索
    msg = Message.query.get(message_id)
    # メッセージが存在し、かつ「書いた本人」からの削除要求である場合のみ実行（他人の悪意ある削除をブロック）
    if msg and msg.login_id == login_id:
        db.session.delete(msg) # DBから消去
        db.session.commit()
        # 📡 同じ部屋の全員の画面に向かって「このメッセージIDの吹き出しを今すぐ画面から消し去って！」とリアルタイムに命令を飛ばす
        emit('message_deleted_from_server', {'message_id': message_id}, room=room)

# =======================================================
# 🚀 6. WEBサーバーの起動
# =======================================================
if __name__ == '__main__':
    # Render本番環境など、システムから指定されたポート番号（PORT）があればそれを使い、なければ5000番で待機
    port = int(os.environ.get("PORT", 5000))
    # Socket.IOによるWEBサーバーをスタート（外部からのアクセス用に 0.0.0.0 で解放）
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)