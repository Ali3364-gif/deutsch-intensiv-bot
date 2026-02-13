import os
from flask import Flask

app = Flask(_name_)

@app.get("/")
def home():
    return "Bot is running ✅"

if _name_ == "_main_":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
