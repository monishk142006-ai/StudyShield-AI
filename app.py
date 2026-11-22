import os
import json
import secrets
from datetime import timedelta

import PyPDF2
from PIL import Image
from dotenv import load_dotenv
from google import genai
from flask import Flask, render_template, request, session, redirect, url_for
from flask_mysqldb import MySQL
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

app = Flask(__name__)

# ---------------- CONFIG ----------------
app.config["UPLOAD_FOLDER"] = "uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# Secret key comes from .env (SECRET_KEY=some-long-random-string).
# If it is missing we fall back to a random key (users get logged out on restart).
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)

# Keep user logged in for 30 days
app.permanent_session_lifetime = timedelta(days=30)

# MySQL Configuration
app.config['MYSQL_HOST'] = os.getenv("MYSQLHOST")
app.config['MYSQL_USER'] = os.getenv("MYSQLUSER")
app.config['MYSQL_PASSWORD'] = os.getenv("MYSQLPASSWORD")
app.config['MYSQL_DB'] = os.getenv("MYSQLDATABASE") or os.getenv("MYSQL_DATABASE")
app.config['MYSQL_PORT'] = int(os.getenv("MYSQLPORT", "3306"))
mysql = MySQL(app)

MODEL = "gemini-2.5-flash"
MAX_TEXT_CHARS = 6000


# ---------------- HELPERS ----------------
def save_study_text(email, text):
    """Store the latest uploaded study text in the database (not the cookie session)."""
    cur = mysql.connection.cursor()
    cur.execute(
        "UPDATE users SET last_pdf_text=%s WHERE email=%s",
        (text, email)
    )
    mysql.connection.commit()
    cur.close()


def get_study_text(email):
    cur = mysql.connection.cursor()
    cur.execute("SELECT last_pdf_text FROM users WHERE email=%s", (email,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row and row[0] else None


def summarize_text(text):
    prompt = f"""
Read the following study material.

Provide:
1. Short Summary
2. Key Points
3. Five Viva Questions

Study Material:

{text[:MAX_TEXT_CHARS]}
"""
    response = client.models.generate_content(model=MODEL, contents=prompt)
    return response.text


# ---------------- HOME ----------------
@app.route('/')
def home():
    return render_template('index.html')


# ---------------- REGISTER ----------------
@app.route('/register', methods=['GET', 'POST'])
def register():

    if request.method == 'POST':

        name = request.form['name'].strip()
        email = request.form['email'].strip().lower()
        password = generate_password_hash(request.form['password'])

        cur = mysql.connection.cursor()

        # Check if email already exists
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        existing_user = cur.fetchone()

        if existing_user:
            cur.close()
            return "Email already registered. Please login or use another email."

        # Insert new user
        cur.execute(
            "INSERT INTO users(name, email, password) VALUES(%s, %s, %s)",
            (name, email, password)
        )

        mysql.connection.commit()
        cur.close()

        return redirect(url_for('login'))

    return render_template('register.html')


# ---------------- LOGIN ----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']

        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cur.fetchone()
        cur.close()

        # user[3] = password column (id, name, email, password, ...)
        if user and check_password_hash(user[3], password):
            session.permanent = True
            session['user'] = email
            return redirect(url_for('dashboard'))
        else:
            return "Invalid Email or Password!"

    return render_template('login.html')


# ---------------- DASHBOARD ----------------
@app.route('/dashboard')
def dashboard():
    if 'user' in session:
        return render_template('dashboard.html', user=session['user'])
    return redirect(url_for('login'))


# ---------------- CHAT AI PAGE ----------------
@app.route('/chat', methods=['GET', 'POST'])
def chat():

    if 'user' not in session:
        return redirect(url_for('login'))

    answer = None

    if request.method == 'POST':

        question = request.form['question']

        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=question
            )

            answer = response.text

            cur = mysql.connection.cursor()
            cur.execute(
                """
                INSERT INTO chat_history(user_email, question, answer)
                VALUES(%s, %s, %s)
                """,
                (session['user'], question, answer)
            )
            mysql.connection.commit()
            cur.close()

        except Exception as e:
            answer = str(e)

    return render_template("chat.html", answer=answer)


# ---------------- LOGOUT ----------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------- CHAT HISTORY ----------------
@app.route('/history')
def history():
    if 'user' not in session:
        return redirect(url_for('login'))

    cur = mysql.connection.cursor()
    cur.execute(
        """
        SELECT question, answer, created_at
        FROM chat_history
        WHERE user_email=%s
        ORDER BY created_at DESC
        """,
        (session['user'],)
    )
    chats = cur.fetchall()
    cur.close()

    return render_template('history.html', chats=chats)


# ---------------- NOTES HISTORY ----------------
@app.route('/notes_history')
def notes_history():

    if 'user' not in session:
        return redirect(url_for('login'))

    cur = mysql.connection.cursor()
    cur.execute(
        """
        SELECT file_name, file_type, uploaded_at
        FROM notes_history
        WHERE user_email=%s
        ORDER BY uploaded_at DESC
        """,
        (session['user'],)
    )
    notes = cur.fetchall()
    cur.close()

    return render_template("notes_history.html", notes=notes)


# ---------------- QUIZ ----------------
@app.route('/quiz')
def quiz():

    if 'user' not in session:
        return redirect(url_for('login'))

    # Read the saved text from the database (works for large PDFs)
    pdf_text = get_study_text(session['user'])

    if not pdf_text:
        return "Please upload a PDF (or image) in the Notes page first."

    prompt = f"""
Generate exactly 10 multiple choice questions.

Return ONLY JSON.

Example:

[
 {{
   "question":"What is Python?",
   "options":[
      "Programming Language",
      "Database",
      "Browser",
      "Operating System"
   ],
   "answer":"Programming Language"
 }}
]

Notes:

{pdf_text[:MAX_TEXT_CHARS]}
"""

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config={"response_mime_type": "application/json"}
        )

        quiz_text = response.text
        quiz_text = quiz_text.replace("```json", "").replace("```", "").strip()

        questions = json.loads(quiz_text)

        return render_template("quiz.html", questions=questions)

    except Exception as e:
        return f"Could not generate quiz: {str(e)}"


# ---------------- QUIZ RESULT ----------------
@app.route('/result', methods=['POST'])
def result():

    if 'user' not in session:
        return redirect(url_for('login'))

    score = 0
    results = []

    # Count how many questions were actually submitted
    total = 0
    for i in range(1, 11):
        if request.form.get(f"correct{i}") is not None:
            total += 1

    for i in range(1, total + 1):

        user = request.form.get(f"q{i}")
        correct = request.form.get(f"correct{i}")

        if user == correct:
            score += 1

        results.append({
            "question": i,
            "user": user,
            "correct": correct
        })

    return render_template(
        "result.html",
        score=score,
        total=total,
        results=results
    )


# ---------------- NOTES (UPLOAD + SUMMARY) ----------------
@app.route('/notes', methods=['GET', 'POST'])
def notes():

    if 'user' not in session:
        return redirect(url_for('login'))

    summary = None

    if request.method == 'POST':

        file = request.files.get('pdf')

        if file and file.filename != "":

            filename = secure_filename(file.filename)

            if not filename:
                return render_template(
                    "notes.html",
                    summary="Invalid file name."
                )

            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            # Save upload history
            cur = mysql.connection.cursor()
            cur.execute(
                """
                INSERT INTO notes_history(user_email, file_name, file_type)
                VALUES(%s, %s, %s)
                """,
                (session['user'], filename, filename.rsplit(".", 1)[-1].lower())
            )
            mysql.connection.commit()
            cur.close()

            try:

                # ---------- PDF ----------
                if filename.lower().endswith(".pdf"):

                    text = ""

                    with open(filepath, "rb") as pdf_file:
                        reader = PyPDF2.PdfReader(pdf_file)

                        for page in reader.pages:
                            page_text = page.extract_text()
                            if page_text:
                                text += page_text + "\n"

                    if not text.strip():
                        summary = (
                            "Could not read any text from this PDF. "
                            "It may be a scanned PDF - try uploading it as an image (JPG/PNG)."
                        )
                    else:
                        # Save in database so /quiz can use it
                        save_study_text(session['user'], text)
                        summary = summarize_text(text)

                # ---------- IMAGE ----------
                elif filename.lower().endswith((".jpg", ".jpeg", ".png")):

                    image = Image.open(filepath)

                    # Step 1: extract the text from the image
                    extract = client.models.generate_content(
                        model=MODEL,
                        contents=[
                            "Extract all the text and study content from this image. "
                            "Return only the extracted content.",
                            image
                        ]
                    )
                    text = extract.text or ""

                    if not text.strip():
                        summary = "Could not read any text from this image."
                    else:
                        # Save in database so /quiz works for images too
                        save_study_text(session['user'], text)
                        summary = summarize_text(text)

                else:
                    summary = "Only PDF, JPG, JPEG and PNG files are supported."

            except Exception as e:
                summary = f"Error: {str(e)}"

    return render_template("notes.html", summary=summary)


# ---------------- RUN APP ----------------
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )