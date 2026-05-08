import os
from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return "Hello! Line Chat App V2 Connection Test Success."

if __name__ == '__main__':
    app.run(debug=True)