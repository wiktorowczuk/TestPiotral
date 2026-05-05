from flask import Flask, render_template, url_for, request, redirect, session
from models import db, UserInfo, Group, GroupStudent, Test, Subject, Grade, Question, TestQuestion, AnswerOption, StudentAttempt, AttemptAnswer, test_groups
from datetime import datetime
import os
from collections import defaultdict

app = Flask(__name__)
app.secret_key = 'tajny-klucz-zadymeczka'

# Baza danych
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db' #ścieżka do bazy
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False #wyłącza śledzenie zmian
db.init_app(app)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        if 'first-name' in request.form: #sprawdza czy formularz dotyczy rejestracji przy pomocy imienia
            role = request.form['role']
            first_name = request.form['first-name']
            last_name = request.form['last-name']
            email = request.form['email']
            password = request.form['password']
            confirm_password = request.form['confirm-password']

            if password != confirm_password:
                return render_template('auth.html', tab='register', error="Hasła się nie zgadzają")

            new_user = UserInfo(
                first_name=first_name,
                last_name=last_name,
                email=email,
                password=password,
                role=role
            )
            db.session.add(new_user)
            db.session.commit()
            return redirect(url_for('register', tab='login'))

        elif 'login-email' in request.form: #sprawdza czy formularz dotyczy logowania przy pomocy emaila
            email = request.form['login-email']
            password = request.form['login-password']
            user1 = UserInfo.query.filter_by(email=email, password=password).first()
            if user1:
                session.permanent = True
                session['user_id'] = user1.id #pamięta kto zalogowany
                session['role'] = user1.role #i z jaką rolą
                session['user_name'] = f"{user1.first_name} {user1.last_name}"
                if user1.role == 'nauczyciel':
                    return redirect(url_for('teacher'))
                else:
                    return redirect(url_for('student'))
            else:
                return render_template('auth.html', tab='login', error="Nieprawidłowy email lub hasło")

    tab = request.args.get('tab', 'register')
    return render_template('auth.html', tab=tab)


@app.route('/student')
def student():
    if session.get('role') != 'student':
        return redirect(url_for('register', tab='login'))

    if 'user_id' in session:
        user_id = session['user_id']
        name = session['user_name']
        role = session['role']

        student_user = UserInfo.query.get(user_id)

        grades = Grade.query.filter_by(user_id=user_id).all()
        dist = {str(val): sum(1 for g in grades if g.value == val) for val in [5, 4, 3, 2]}
        average = round(sum(g.value for g in grades) / len(grades), 2) if grades else 0

        subject_count = Subject.query.count()
        subjects = Subject.query.all()

        #Pobierz ID grup studenta
        group_ids = [g.id for g in student_user.groups]

        #Testy przypisane do grup
        allowed_tests = Test.query \
            .join(test_groups) \
            .filter(test_groups.c.group_id.in_(group_ids)) \
            .all()

        #ID testów, które już podjął
        taken_test_ids = set(g.attempt_id for g in grades if g.attempt_id is not None)

        #Filtrowanie tylko tych dostępnych z przypisanych
        available_tests = [t for t in allowed_tests if t.id not in taken_test_ids]
        test_count = len(available_tests)


        #Last attempts data
        attempts = StudentAttempt.query.filter_by(student_id=user_id).order_by(StudentAttempt.id.desc()).limit(5).all()
        last_attempts = [
            {
                "attempt_id": a.id,
                "test_title": a.test.title,
                "subject_name": a.test.subject.subject_name,
                "score": int(a.score),
                # "date": a.test.description[:10] - to dawalo tylko 10 pierwszych znakow w opisie na stronie glownej ucznia
                "description": a.test.description
            }
            for a in attempts
        ]

        return render_template("student.html", name=name, role=role, grades=grades,
                               dist=dist, average=average,
                               subject_count=subject_count, subjects=subjects,
                               test_count=test_count, last_attempts=last_attempts)

    return redirect(url_for('register', tab='login'))


@app.route('/student/tests')
def available_tests():
    if session.get('role') != 'student':
        return redirect(url_for('register', tab='login'))

    user_id = session['user_id']
    student_user = UserInfo.query.get(user_id)

    taken = db.session.query(StudentAttempt.test_id).filter_by(student_id=user_id).subquery()

    group_ids = [g.id for g in student_user.groups]

    if not group_ids:
        tests = []
    else:
        tests = Test.query\
            .join(test_groups)\
            .filter(test_groups.c.group_id.in_(group_ids))\
            .filter(~Test.id.in_(taken))\
            .all()

    return render_template('student_tests.html', tests=tests)


@app.route('/student/test/<int:test_id>', methods=['GET', 'POST'])
def student_test(test_id):
    if session.get('role') != 'student':
        return redirect(url_for('register', tab='login'))

    if request.method == 'GET':
        session.pop('current_question', None)
        session.pop('answers', None)

    test = Test.query.get_or_404(test_id)
    test_questions = TestQuestion.query.filter_by(test_id=test_id).all()
    question_map = {tq.question_id: tq.points for tq in test_questions}
    questions = Question.query.filter(Question.id.in_(question_map.keys())).all()

    if not questions:
        return render_template('student_take_test.html', test=test, questions=[], current_question=0, empty=True)


    if 'answers' not in session:
        session['answers'] = {}
    answers = session['answers']

    current_question = session.get('current_question', 0)

    if request.method == 'POST':
        selected_option = request.form.get(f'question_{questions[current_question].id}')
        if selected_option:
            answers[str(questions[current_question].id)] = int(selected_option)
            session['answers'] = answers

        action = request.form.get('action')
        if action == 'next' and current_question < len(questions) - 1:
            current_question += 1
        elif action == 'prev' and current_question > 0:
            current_question -= 1
        elif action == 'submit':
            score = 0
            total = 0
            results = []

            for q in questions:
                correct_option = next((opt for opt in q.answer_options if opt.is_correct), None)
                is_correct = str(q.id) in answers and answers[str(q.id)] == correct_option.id if correct_option else False
                points = question_map.get(q.id, 1)
                if is_correct:
                    score += points
                total += points
                results.append({"question": q, "correct": is_correct})

            new_attempt = StudentAttempt(
                student_id=session['user_id'],
                test_id=test.id,
                score=score
            )
            db.session.add(new_attempt)
            db.session.commit()

                        #Zapisz odpowiedzi ucznia do AttemptAnswer
            for qid_str, selected_option_id in answers.items():
                new_answer = AttemptAnswer(
                    attempt_id=new_attempt.id,
                    answer_option_id=selected_option_id
                )
                db.session.add(new_answer)
            db.session.commit()



            percentage = (score / total) * 100 if total > 0 else 0
            if percentage >= 90:
                grade_value = 5
            elif percentage >= 75:
                grade_value = 4
            elif percentage >= 50:
                grade_value = 3
            else:
                grade_value = 2

            new_grade = Grade(
                value=grade_value,
                user_id=session['user_id'],
                subject_id=test.subject_id,
                attempt_id=new_attempt.id
            )
            db.session.add(new_grade)
            db.session.commit()

            

            session.pop('current_question', None)
            session.pop('answers', None)

            return redirect(url_for('student_test_result', attempt_id=new_attempt.id))

        session['current_question'] = current_question

    return render_template(
        'student_take_test.html',
        test=test,
        questions=questions,
        current_question=current_question
    )


@app.route('/student/test/result/<int:attempt_id>')
def student_test_result(attempt_id):
    attempt = StudentAttempt.query.get_or_404(attempt_id)
    if session.get('role') != 'student' or attempt.student_id != session.get('user_id'):
        return redirect(url_for('register', tab='login'))

    test = attempt.test
    test_questions = TestQuestion.query.filter_by(test_id=test.id).all()
    question_map = {tq.question_id: tq.points for tq in test_questions}
    questions = Question.query.filter(Question.id.in_(question_map.keys())).all()

    results = []
    attempt_answers = {a.answer_option.question_id: a.answer_option_id for a in attempt.answers}

    for q in questions:
        correct_option = next((opt for opt in q.answer_options if opt.is_correct), None)
        chosen_id = attempt_answers.get(q.id)
        is_correct = (chosen_id == correct_option.id) if correct_option else False
        results.append({
            "question": q,
            "correct": is_correct,
            "selected_option": chosen_id,
            "correct_option": correct_option.id if correct_option else None
        })



    return render_template(
        'student_test_result.html',
        test=test,
        score=int(attempt.score),
        total=sum(question_map.values()),
        results=results
    )


@app.route('/teacher') #jesli rola teacher to zostaje na stronie teacher
def teacher():
    if session.get('role') != 'nauczyciel':
        return redirect(url_for('register', tab='login'))
    elif 'user_id' in session:
        name = session.get('user_name')
        role = session.get('role')
        return render_template('teacher.html', name=name, role=role)
    return redirect(url_for('register', tab='login'))


@app.route('/teacher/studentlist_teacher')
def studentlist_teacher():
    if session.get('role') != 'nauczyciel':
        return redirect(url_for('register', tab='login'))
    # pobierz listę wszystkich studentów lub tych w grupach nauczyciela
    students = UserInfo.query.filter_by(role='student').all()
    return render_template('studentlist_teacher.html', students=students)


@app.route('/grades', methods=['GET', 'POST'])
def grades():
    if session.get('role') != 'nauczyciel':
        return redirect(url_for('register', tab='login'))

    teacher_id = session['user_id']

    if request.method == 'POST':

        try:
            grade_content = int(request.form['grade'])
            if grade_content < 2 or grade_content > 5:
                return "Ocena musi być z zakresu 2–5"

            new_grade = Grade(
                value=grade_content,
                user_id=session['user_id'],
                subject_id=int(request.form['subject_id']),
                added_date=datetime.utcnow(),
                attempt_id=int(request.form['attempt_id'])  #jeśli używasz attempt_id
            )
            db.session.add(new_grade)
            db.session.commit()
            return redirect(url_for('grades'))
        except ValueError:
            return "Wartość oceny musi być liczbą całkowitą"
        except Exception as e:
            return f"Wystąpił błąd: {e}"

    #GET — pobierz oceny z testów tego nauczyciela
    grades = Grade.query \
        .join(StudentAttempt, Grade.attempt_id == StudentAttempt.id) \
        .join(Test, StudentAttempt.test_id == Test.id) \
        .filter(Test.teacher_id == teacher_id) \
        .all()

    return render_template('grades.html', grades=grades)


@app.route('/groups_teacher', methods=['GET', 'POST']) #tworzenie grupy, dodawanie studentów i ich usuwanie
def groups_teacher():
    if 'user_id' not in session or session.get('role') != 'nauczyciel':
        return redirect(url_for('register', tab='login'))

    teacher_id = session['user_id']
    groups = Group.query.filter_by(teacher_id=teacher_id).all()
    students = UserInfo.query.filter_by(role='student').all()

    selected_group_id = None
    selected_student_id = None

    if request.method == 'POST':
        form = request.form

        if 'group_name' in form:
            name = form['group_name'].strip()
            if name:
                new_group = Group(name=name, teacher_id=teacher_id)
                db.session.add(new_group)
                db.session.commit()
            return redirect(url_for('groups_teacher'))

        elif 'action' in form and 'group_id' in form and 'student_id' in form:
            group_id = int(form['group_id'])
            student_id = int(form['student_id'])
            action = form['action']

            selected_group_id = group_id
            selected_student_id = student_id

            group = Group.query.filter_by(id=group_id, teacher_id=teacher_id).first()
            student = UserInfo.query.filter_by(id=student_id, role='student').first()

            if group and student:
                if action == 'add' and student not in group.students:
                    group.students.append(student)
                    db.session.commit()
                elif action == 'remove' and student in group.students:
                    group.students.remove(student)
                    db.session.commit()

    return render_template(
        'groups_teacher.html',
        groups=groups,
        students=students,
        selected_group_id=selected_group_id,
        selected_student_id=selected_student_id
    )

@app.route('/teacher/groups/<int:group_id>/delete', methods=['POST'])
def delete_group(group_id):
    if session.get('role') != 'nauczyciel':
        return redirect(url_for('register', tab='login'))

    group = Group.query.filter_by(id=group_id, teacher_id=session['user_id']).first()
    if group:
        #Usuń powiązania z uczniami
        group.students.clear()

        #Usuń powiązania z testami (test_groups)
        group.tests.clear()

        db.session.delete(group)
        db.session.commit()

    return redirect(url_for('groups_teacher'))



@app.route('/teacher/tests') #tworzenie testów, edycja i usuwanie
def teacher_tests():
    if session.get('role') != 'nauczyciel':
        return redirect(url_for('register', tab='login'))

    teacher_id = session.get('user_id')
    tests = Test.query.filter_by(teacher_id=teacher_id).all()
    return render_template('teacher_tests.html', tests=tests)


@app.route('/teacher/tests/new', methods=['GET', 'POST']) #strona od tworzenia (tylko tytul opis i przedmiot, reszta w edycji)
def create_test():
    if session.get('role') != 'nauczyciel':
        return redirect(url_for('register', tab='login'))

    if request.method == 'POST':     # jeśli formularz został wysłany – tworzymy nowy test
        title = request.form['title']
        description = request.form['description']
        subject_id = int(request.form['subject_id'])
        teacher_id = session['user_id']

        new_test = Test(
            title=title,
            description=description,
            subject_id=subject_id,
            teacher_id=teacher_id
        )
        db.session.add(new_test)
        db.session.commit() # zapisujemy test do bazy
        return redirect(url_for('edit_test', test_id=new_test.id)) #od razu przechodzi do edycji bo tu narazie ogarnia pytania

    subjects = Subject.query.all()
    return render_template('create_test.html', subjects=subjects)


@app.route('/teacher/tests/<int:test_id>') #otworz
def view_test(test_id):
    if session.get('role') != 'nauczyciel':
        return redirect(url_for('register', tab='login'))

    test = Test.query.get_or_404(test_id) # pobiera obiekt z bazy lub zwraca 404 jeśli nie istnieje.

    return render_template('view_test.html', test=test)


@app.route('/teacher/tests/<int:test_id>/edit', methods=['GET', 'POST']) #edytuj test
def edit_test(test_id):
    if session.get('role') != 'nauczyciel':
        return redirect(url_for('register', tab='login'))

    test = Test.query.get_or_404(test_id)
    subjects = Subject.query.all()

    if request.method == 'POST':
        test.title = request.form['title']
        test.description = request.form['description']
        test.subject_id = int(request.form['subject_id'])

        selected_group_ids = request.form.getlist('groups')  # z formularza
        test.groups = Group.query.filter(Group.id.in_(selected_group_ids)).all()

        db.session.commit()
        return redirect(url_for('teacher_tests'))

    groups = Group.query.filter_by(teacher_id=session['user_id']).all()
    return render_template('edit_test.html', test=test, subjects=subjects, groups=groups)


@app.route('/teacher/tests/<int:test_id>/delete') #usun
def delete_test(test_id):
    if session.get('role') != 'nauczyciel':
        return redirect(url_for('register', tab='login'))

    test = Test.query.get_or_404(test_id)

    #Odepnij test od grup
    test.groups.clear()

    #Usuń powiązane pytania
    for tq in list(test.test_questions):
        db.session.delete(tq)

    #Usuń próby uczniów i ich odpowiedzi + oceny
    for attempt in list(test.attempts):
        #odpowiedzi
        for ans in list(attempt.answers):
            db.session.delete(ans)
        #oceny
        grades = Grade.query.filter_by(attempt_id=attempt.id).all()
        for g in grades:
            db.session.delete(g)
        #sama próba
        db.session.delete(attempt)


    db.session.delete(test)
    db.session.commit()

    return redirect(url_for('teacher_tests'))



@app.route('/teacher/tests/<int:test_id>/add_question', methods=['GET', 'POST'])

def add_question_to_test(test_id):
    test = Test.query.get_or_404(test_id)
    existing_questions = Question.query.all() 

    if request.method == 'POST':
        form = request.form

        #Dodanie istniejącego pytania po ID
        if 'question_id' in form and 'points' in form and 'question_text' not in form:
            try:
                question_id = int(form['question_id'])
                points = int(form['points'])
                existing_question = Question.query.get(question_id)

                if existing_question:
                    tq = TestQuestion(test_id=test.id, question_id=question_id, points=points)
                    db.session.add(tq)
                    db.session.commit()
                    return redirect(url_for('edit_test', test_id=test.id))
                else:
                    return "Pytanie o podanym ID nie istnieje.", 404
            except ValueError:
                return "Błąd danych wejściowych.", 400

        #Dodanie nowego pytania i jego odpowiedzi
        if 'question_text' in form:
            text = form['question_text']
            points = int(form['points'])

            correct_index = form.get("is_correct")
            if correct_index is None or not correct_index.isdigit() or int(correct_index) not in range(1, 5):
                return "Musisz zaznaczyć dokładnie jedną poprawną odpowiedź.", 400
            correct_index = int(correct_index)


            new_question = Question(text=text)
            db.session.add(new_question)
            db.session.flush()

            correct_index = int(form.get("is_correct") or -1)
            for i in range(1, 5):
                ans_text = form.get(f'answer_{i}')
                is_correct = (i == correct_index)

                if ans_text:
                    option = AnswerOption(text=ans_text, is_correct=is_correct, question=new_question)
                    db.session.add(option)

            tq = TestQuestion(test_id=test.id, question_id=new_question.id, points=points)
            db.session.add(tq)
            db.session.commit()
            return redirect(url_for('edit_test', test_id=test.id))
        
        existing_questions = Question.query.order_by(Question.id.desc()).limit(10).all()


    return render_template('add_question.html', test=test, existing_questions=existing_questions)


@app.route('/teacher/questions/<int:question_id>/edit', methods=['GET', 'POST']) #edytuj pytanie
def edit_question(question_id):
    question = Question.query.get_or_404(question_id)

    if request.method == 'POST':
        question.text = request.form['question_text']
        db.session.commit()

        #Aktualizacja odpowiedzi
        for option in question.answer_options:
            option.text = request.form.get(f'option_{option.id}')
            option.is_correct = f'is_correct_{option.id}' in request.form

        db.session.commit()
        return redirect(url_for('teacher_tests'))

    return render_template('edit_question.html', question=question)


@app.route('/teacher/tests/<int:test_id>/remove_question/<int:question_id>') #usun pytanie
def remove_question_from_test(test_id, question_id):
    tq = TestQuestion.query.filter_by(test_id=test_id, question_id=question_id).first()
    if tq:
        db.session.delete(tq)
        db.session.commit()

    return redirect(url_for('edit_test', test_id=test_id))


@app.route('/teacher/tests/<int:test_id>/results')
def teacher_test_results(test_id):
    if session.get('role') != 'nauczyciel':
        return redirect(url_for('register', tab='login'))

    test = Test.query.get_or_404(test_id)

    # Zbierz results dla szablonu
    results = []
    for a in test.attempts:
        results.append({
            'student': a.student,
            'score': a.score,
            'total_points': test.total_points
        })

    return render_template(
        'teacher_test_results.html',
        test=test,
        results=results
    )



@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/student/subjects')
def student_subjects():
    if session.get('role') != 'student':
        return redirect(url_for('register', tab='login'))

    user = UserInfo.query.get(session['user_id'])

    #Pobierz testy przypisane do grup
    group_ids = [g.id for g in user.groups]
    tests = Test.query \
        .join(test_groups) \
        .filter(test_groups.c.group_id.in_(group_ids)) \
        .all()

    #Zbiór unikalnych przedmiotów
    subjects = Subject.query.order_by(Subject.subject_name).all()

    #Pobierz wszystkie oceny ucznia, posortowane malejąco wg daty
    grades = Grade.query \
        .filter_by(user_id=user.id) \
        .order_by(Grade.added_date.desc()) \
        .all()
    #grades = Grade.query.join(StudentAttempt).join(Test).filter(Test.teacher_id == session['user_id']).all()
    
    grades_by_subject = defaultdict(list)
    for g in grades:
        grades_by_subject[g.subject_id].append(g)



    return render_template(
        'student_subjects.html',
        subjects=subjects,
        grades_by_subject=grades_by_subject
    )




if __name__ == "__main__":
    with app.app_context():
        db.create_all()


        if Subject.query.count() == 0:
            default_subjects = [
                Subject(subject_name='Matematyka'),
                Subject(subject_name='Fizyka'),
                Subject(subject_name='Biologia'),
                Subject(subject_name='Informatyka'),
                Subject(subject_name='Chemia'),
            ]
            db.session.bulk_save_objects(default_subjects)
            db.session.commit()
            print("Dodano domyślne przedmioty do bazy danych.")

    app.run(debug=True)
