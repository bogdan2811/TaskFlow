# Folosim o imagine oficială Python, varianta "slim" pentru a avea un container de dimensiuni mici
FROM python:3.11-slim

# Evită scrierea fișierelor .pyc pe disc și forțează output-ul în consolă (bun pentru logurile din GCP)
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Setăm directorul de lucru în interiorul containerului
WORKDIR /app

# Copiem doar fișierul cu dependențe prima dată (pentru a folosi eficient cache-ul Docker)
COPY requirements.txt .

# Instalăm dependențele
RUN pip install --no-cache-dir -r requirements.txt

# Copiem restul fișierelor aplicației
COPY . .

# Setăm portul default (Cloud Run va suprascrie asta dacă e nevoie, dar e o practică bună)
ENV PORT 8080

# Pornim aplicația folosind gunicorn. 
# --bind :$PORT îi spune să asculte pe portul setat de Cloud Run
CMD exec uvicorn app:app --host 0.0.0.0 --port $PORT