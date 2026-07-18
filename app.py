from PIL import Image
import os
import json
import PyPDF2
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
print("API KEY:", os.getenv("GEMINI_API_KEY"))



from flask import Flask, render_template, request, session, redirect, url_for
from flask_mysqldb import MySQL
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = "uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
from datetime import timedelta

app.secret_key = "studyshield_secret_key"

# Keep user logged in for 30 days
app.permanent_session_lifetime = timedelta(days=30)
app.secret_key = "studyshield_secret_key"

# MySQL Configuration
print("MYSQLHOST:", os.getenv("MYSQLHOST"))
print("MYSQLPORT:", os.getenv("MYSQLPORT"))
print("MYSQLUSER:", os.getenv("MYSQLUSER"))
print("MYSQLDATABASE:", os.getenv("MYSQLDATABASE"))
print("MYSQL_DATABASE:", os.getenv("MYSQL_DATABASE"))

app.config['MYSQL_HOST'] = os.getenv("MYSQLHOST")
app.config['MYSQL_USER'] = os.getenv("MYSQLUSER")
app.config['MYSQL_PASSWORD'] = os.getenv("MYSQLPASSWORD")
app.config['MYSQL_DB'] = os.getenv("MYSQLDATABASE") or os.getenv("MYSQL_DATABASE")
app.config['MYSQL_PORT'] = int(os.getenv("MYSQLPORT", "3306"))
mysql = MySQL(app)

# ---------------- HOME ----------------
@app.route('/')
def home():
    return render_template('index.html')


# ---------------- REGISTER ----------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])

        cur = mysql.connection.cursor()
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
        email = request.form['email']
        password = request.form['password']

        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT * FROM users WHERE email=%s",
            (email,)
        )

        user = cur.fetchone()
        cur.close()

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
    else:
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
                model="gemini-2.5-flash",
                contents=question
            )

            answer = response.text

            cur = mysql.connection.cursor()

            cur.execute("""
                INSERT INTO chat_history(user_email, question, answer)
                VALUES(%s, %s, %s)
            """, (
                session['user'],
                question,
                answer
            ))

            mysql.connection.commit()
            cur.close()

        except Exception as e:

            answer = f"Error: {str(e)}"

    return render_template(
        'chat.html',
        answer=answer
    )
# ---------------- LOGOUT ----------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))  
  

#--historuy
@app.route('/history')
def history():
    if 'user' not in session:
        return redirect(url_for('login'))

    print("Logged in user:", session['user'])

    cur = mysql.connection.cursor()

    cur.execute(
    "SELECT question, answer, created_at FROM chat_history ORDER BY created_at DESC")





    chats = cur.fetchall()

    print("Chats:", chats)

    cur.close()

    return render_template('history.html', chats=chats)


@app.route('/notes_history')
def notes_history():

    if 'user' not in session:
        return redirect(url_for('login'))

    cur = mysql.connection.cursor()

    cur.execute("""
    SELECT file_name,file_type,uploaded_at
    FROM notes_history
    WHERE user_email=%s
    ORDER BY uploaded_at DESC
    """,(session['user'],))

    notes = cur.fetchall()

    cur.close()

    return render_template(
        "notes_history.html",
        notes=notes
    )






import re
@app.route('/quiz')
def quiz():

    if 'user' not in session:
        return redirect(url_for('login'))

    pdf_text = session.get("pdf_text")

    if not pdf_text:
        return "Please upload a PDF first."

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

{pdf_text}
"""

    try:

        response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
          )

        quiz_text = response.text

        quiz_text = quiz_text.replace("```json", "")
        quiz_text = quiz_text.replace("```", "")
        quiz_text = quiz_text.strip()

        questions = json.loads(quiz_text)

        return render_template(
            "quiz.html",
            questions=questions
        )

    except Exception as e:

        return str(e)


@app.route('/result', methods=['POST'])
def result():

    if 'user' not in session:
        return redirect(url_for('login'))

    score = 0
    total = 10

    results = []

    for i in range(1,11):

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



@app.route('/notes', methods=['GET', 'POST'])
def notes():

    if 'user' not in session:
        return redirect(url_for('login'))

    summary = None

    if request.method == 'POST':

        file = request.files['pdf']

        if file:

            filename = file.filename.lower()

            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filepath)

            cur = mysql.connection.cursor()
            cur.execute("""
                INSERT INTO notes_history(user_email, file_name, file_type)
                VALUES(%s,%s,%s)
            """, (
                session['user'],
                file.filename,
                filename.split(".")[-1]
            ))

            mysql.connection.commit()
            cur.close()

            try:

                # ---------- PDF ----------
                if filename.endswith('.pdf'):

                    text = ""

                    with open(filepath, "rb") as pdf_file:

                        reader = PyPDF2.PdfReader(pdf_file)

                        for page in reader.pages:
                            page_text = page.extract_text()

                            if page_text:
                                text += page_text

                    session["pdf_text"] = text

                    prompt = f"""
Read the following study material.

Provide:

1. Short Summary

2. Key Points

3. Five Viva Questions

Study Material:

{text[:6000]}
"""

                    response = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=prompt
                    )

                    summary = response.text

                # ---------- IMAGE ----------
                elif filename.endswith(('.jpg', '.jpeg', '.png')):

                    image = Image.open(filepath)

                    response = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=[
                            "Read this study note image. Give a summary, important key points and five viva questions.",
                            image
                        ]
                    )

                    summary = response.text

                else:

                    summary = "Only PDF, JPG, JPEG and PNG files are supported."

            except Exception as e:

                return render_template(
                    "error.html",
                    error=str(e)
                )

    return render_template(
        "notes.html",
        summary=summary
    )






# ---------------- RUN APP ----------------
import os

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )