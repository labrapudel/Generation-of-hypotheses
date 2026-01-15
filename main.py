from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from groq import Groq
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import PyPDF2
from io import BytesIO
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'supersecretkey123456789'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = None  # Убирает "Please log in to access this page"

client = Groq(api_key="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")

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

    def get_id(self):
        return str(self.id)

class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    abstract = db.Column(db.Text)
    authors = db.Column(db.String(800))
    pdf_url = db.Column(db.String(500))
    source = db.Column(db.String(50))
    publication_date = db.Column(db.String(50))  # Новое поле для даты

class Generation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    article_title = db.Column(db.String(500))
    article_source = db.Column(db.String(50))
    hypotheses = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def translate_text(text):
    if not text or len(text.strip()) < 20:
        return text
    prompt = f"Переведи следующий научный текст на русский язык точно и естественно, сохраняя термины:\n\n{text[:4500]}"
    try:
        resp = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
            temperature=0.3,
            max_tokens=1200
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("Translate error:", str(e))
        return text

def fetch_from_arxiv(query="metallurgy", max_results=12):
    url = f"http://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
    try:
        r = requests.get(url, timeout=15)
        root = ET.fromstring(r.content)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        articles = []
        for entry in root.findall('atom:entry', ns):
            title = entry.find('atom:title', ns).text.strip() if entry.find('atom:title', ns) is not None else "Без названия"
            abstract = entry.find('atom:summary', ns).text.strip() if entry.find('atom:summary', ns) is not None else ""
            authors = ", ".join([a.find('atom:name', ns).text for a in entry.findall('atom:author', ns)])
            pdf_link = entry.find("atom:link[@title='pdf']", ns)
            pdf_url = pdf_link.get('href') if pdf_link is not None else "#"
            date = entry.find('atom:published', ns).text.strip() if entry.find('atom:published', ns) is not None else "Не указана"
            if len(abstract.strip()) < 50: continue
            articles.append(Article(
                title=title,
                abstract=abstract,
                authors=authors,
                pdf_url=pdf_url,
                source="arXiv",
                publication_date=date
            ))
        return articles
    except Exception as e:
        print("ArXiv error:", str(e))
        return []

def fetch_articles(area="metallurgy"):
    Article.query.delete()
    db.session.commit()

    # OpenAlex
    url = "https://api.openalex.org/works"
    params = {"filter": f"title.search:{area}", "per_page": 20, "sort": "publication_date:desc"}
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        for work in data.get("results", []):
            abstract = work.get("abstract") or ""
            if len(abstract.strip()) < 50: continue
            article = Article(
                title=work.get("title", "Без названия"),
                abstract=abstract,
                authors=", ".join([a["author"]["display_name"] for a in work.get("authorships", [])]),
                pdf_url=work.get("open_access", {}).get("oa_url") or "#",
                source="OpenAlex",
                publication_date=work.get("publication_date", "Не указана")
            )
            db.session.add(article)
    except Exception as e:
        print("OpenAlex error:", str(e))

    arxiv_articles = fetch_from_arxiv(area.replace(" ", "+"))
    for art in arxiv_articles:
        db.session.add(art)

    db.session.commit()

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if not username or not password:
            flash('Заполните все поля', 'error')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('Логин занят', 'error')
            return redirect(url_for('register'))
        user = User(username=username, password=password)
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            login_user(user)
            return redirect(url_for('home'))
        else:
            flash('Неверный логин или пароль', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def home():
    page = request.args.get('page', 1, type=int)
    area = request.args.get('area', 'metallurgy')
    fetch_articles(area)
    pagination = Article.query.paginate(page=page, per_page=10, error_out=False)
    articles = pagination.items
    areas = ['metallurgy', 'physics', 'chemistry', 'biology', 'computer science', 'materials science']
    return render_template('home.html', articles=articles, pagination=pagination, areas=areas, current_area=area)

@app.route('/article/<int:id>')
@login_required
def article(id):
    art = Article.query.get_or_404(id)
    translated = translate_text(art.abstract)
    return render_template('article.html', article=art, translated=translated)

@app.route('/generate/<int:id>')
@login_required
def generate(id):
    try:
        article = Article.query.get_or_404(id)
        text = article.abstract or article.title
        prompt = f"""
Ты — ведущий научный исследователь мирового уровня в области металлургии и материаловедения. 
На основе данной статьи сгенерируй ровно 3 выдающиеся, оригинальные и высокоценные научные гипотезы, которые:

1. Основаны на глубоком анализе текста.
2. Предлагают реальный прорыв или значительное улучшение.
3. Проверяемы экспериментально (укажи возможный способ проверки).
4. Имеют потенциал для публикации в журнале уровня Nature Materials, Advanced Materials, Acta Materialia.
5. Используют современные тренды (AI, наноматериалы, устойчивость, новые сплавы и т.д.), если это уместно.
6. Каждая гипотеза — конкретное смелое утверждение + 1–2 предложения обоснования + способ проверки.

Пиши строго на русском языке, нумеруй 1., 2., 3.
Будь креативным, но оставайся в рамках строгой науки.

Текст статьи:
{text[:4500]}
"""
        resp = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
            temperature=0.75,
            max_tokens=700
        )
        hypotheses = resp.choices[0].message.content.strip()

        # Сохраняем в историю
        gen = Generation(
            user_id=current_user.id,
            article_title=article.title,
            article_source=article.source,
            hypotheses=hypotheses
        )
        db.session.add(gen)
        db.session.commit()

        return jsonify({
            'success': True,
            'hypotheses': hypotheses
        })

    except Exception as e:
        print("Generate error:", str(e))  # логируем в терминал
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/history')
@login_required
def history():
    gens = Generation.query.filter_by(user_id=current_user.id).order_by(Generation.created_at.desc()).limit(20).all()
    return render_template('history.html', generations=gens)

@app.route('/custom_generate', methods=['GET', 'POST'])
@login_required
def custom_generate():
    if request.method == 'POST':
        url = request.form.get('url')
        file = request.files.get('file')
        text = ""
        if file and file.filename.endswith('.pdf'):
            try:
                pdf_reader = PyPDF2.PdfReader(BytesIO(file.read()))
                for page in pdf_reader.pages:
                    text += page.extract_text() or ""
            except Exception as e:
                return jsonify({'success': False, 'error': f'Ошибка чтения PDF: {str(e)}'})
        elif url:
            try:
                r = requests.get(url, timeout=10)
                r.encoding = 'utf-8'
                soup = BeautifulSoup(r.text, 'html.parser')
                text = ' '.join(p.text for p in soup.find_all('p') if p.text.strip())
            except Exception as e:
                return jsonify({'success': False, 'error': f'Ошибка загрузки URL: {str(e)}'})
        if not text.strip():
            return jsonify({'success': False, 'error': 'Не удалось извлечь текст'})

        prompt = f"""
Ты — ведущий научный исследователь мирового уровня в области материаловедения и металлургии. 
На основе данного текста сгенерируй ровно 3 выдающиеся, оригинальные и высокоценные научные гипотезы, которые:

1. Основаны на глубоком анализе текста.
2. Предлагают реальный прорыв или значительное улучшение.
3. Проверяемы экспериментально (укажи способ проверки).
4. Имеют потенциал для публикации в журнале уровня Nature Materials, Advanced Materials, Acta Materialia.
5. Используют современные тренды (AI, наноматериалы, устойчивость, новые сплавы и т.д.), если это уместно.
6. Каждая гипотеза — конкретное смелое утверждение + 1–2 предложения обоснования + способ проверки.

Пиши строго на русском языке, нумеруй 1., 2., 3.
Будь креативным, но оставайся в рамках строгой науки.

Текст:
{text[:4500]}
"""
        try:
            resp = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.1-8b-instant",
                temperature=0.75,
                max_tokens=700
            )
            hypotheses = resp.choices[0].message.content.strip()
            gen = Generation(
                user_id=current_user.id,
                article_title="Пользовательский документ",
                article_source="Загружено вручную",
                hypotheses=hypotheses
            )
            db.session.add(gen)
            db.session.commit()
            return jsonify({'success': True, 'hypotheses': hypotheses})
        except Exception as e:
            return jsonify({'success': False, 'error': f'Ошибка генерации: {str(e)}'})
    return render_template('custom_generate.html')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)