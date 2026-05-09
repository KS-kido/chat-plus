import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# --- データベース設定（二刀流ロジック） ---
# Render上の環境変数 'DATABASE_URL' を探し、なければローカル用の 'sqlite:///chat.db' を使う
database_url = os.environ.get('DATABASE_URL')

if database_url:
    # RenderのPostgreSQL用設定（postgres:// を postgresql:// に変換が必要な場合への対応）
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # ローカルPC用設定
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- データベースモデル（ここに必要な項目を足していきます） ---
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(500), nullable=False)

# 起動時にテーブルを作成
with app.app_context():
    db.create_all()

@app.route('/')
def hello():
    return "DB Connection Success! Ready for Chat App V2."

if __name__ == '__main__':
    app.run(debug=True)