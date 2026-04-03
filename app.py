import os
from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello_world():
    # Preluăm versiunea dintr-o variabilă de mediu (opțional, poți seta asta în CI/CD)
    version = os.environ.get('APP_VERSION', 'v.Necunoscuta')
    return f"Salut! Acesta este un test pentru pipeline-ul Cloud Run. Versiune aplicație: {version}\n"

if __name__ == "__main__":
    # Cloud Run injectează automat portul în variabila de mediu PORT
    port = int(os.environ.get('PORT', 8080))
    # Ascultăm pe 0.0.0.0 pentru a permite conexiuni externe containerului
    app.run(host='0.0.0.0', port=port)